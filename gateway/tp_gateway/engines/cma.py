"""CMA engine — anthropic SDK beta.sessions (CONTRACT.md §6).

Event delivery is stream-first, then reconcile: open the SSE stream, fetch the
full events.list, and dedupe/upsert by (id, processed_at) — CMA's stream has
NO server replay, so the gateway's SQLite is the replay surface and this
adapter only guarantees at-least-once delivery (the gateway upserts by id).
Every reconnect repeats the same consolidation.

All SDK calls go through the injected client so a fake client can drive the
consolidation logic in unit tests (tests/test_cma_consolidation.py). The SDK
routes beta headers itself: sessions/* under managed-agents-2026-04-01,
memory_stores/* under agent-memory-2026-07-22 — never both on a memory call.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from tp_gateway.config import Settings
from tp_gateway.engines.base import RunHandle, RunSpec, UsageNorm, WireEvent

logger = logging.getLogger(__name__)

_TERMINAL_TYPES = {"session.status_terminated", "session.deleted"}
_RECONNECT_DELAY_S = 1.0

MEMORY_INSTRUCTIONS = (
    "Career memory for this candidate. Layout (relative paths): profile/master.md "
    "(merged narrative profile); profile/claims/<slug>.md (ONE claim per file: statement, "
    "evidence, source resume|answer|inferred, status user-confirmed|unverified, date); "
    "qa/<date>-<slug>.md (distilled Q&A); applications/<slug>/research.md and "
    "applications/<slug>/gap-analysis.md (REQUIRED before your first submit_draft); "
    "applications/<slug>/notes.md; preferences.md. Read memory before asking anything; "
    "write every learned claim as you go. Never store secrets."
)

_KICKOFF_TEMPLATE = """New application run. Work your charter: research, gap-driven discovery interview, grounded drafting.

## CANDIDATE RESUME (verbatim — this is a grounding source)

{resume}

## TARGET JOB
{job_section}

Begin by publishing your plan with update_plan, then read your memory before asking the candidate anything."""


def _event_to_dict(ev: Any) -> dict[str, Any]:
    """SDK pydantic model or plain dict (fake clients) -> wire dict."""
    if isinstance(ev, dict):
        return dict(ev)
    return ev.model_dump(mode="json")


class CmaEngine:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client  # injected fake in tests; real AsyncAnthropic lazily

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    # ── adapter protocol ─────────────────────────────────────────────────────

    async def create_run(self, spec: RunSpec) -> RunHandle:
        s = self._settings
        if not (s.cma_agent_id and s.cma_environment_id and s.cma_memory_store_id):
            # memory is load-bearing (charter + judge contract files) — refuse a
            # partially configured .env rather than silently run memoryless
            raise RuntimeError(
                "CMA engine not configured: CMA_AGENT_ID / CMA_ENVIRONMENT_ID / "
                "CMA_MEMORY_STORE_ID missing — run infra/cma/setup.sh"
            )
        agent: dict[str, Any] = {"type": "agent", "id": s.cma_agent_id}
        if s.cma_agent_version is not None:
            agent["version"] = s.cma_agent_version
        kwargs: dict[str, Any] = {
            "resources": [
                {
                    "type": "memory_store",
                    "memory_store_id": s.cma_memory_store_id,
                    "access": "read_write",
                    "instructions": MEMORY_INSTRUCTIONS,
                }
            ]
        }
        session = await self.client.beta.sessions.create(
            agent=agent,
            environment_id=s.cma_environment_id,
            metadata={"run_id": spec.run_id, "engine": "cma"},
            title=spec.title,
            **kwargs,
        )
        session_id = session["id"] if isinstance(session, dict) else session.id
        workspace = s.cma_workspace_id or "default"
        logger.info(
            "CMA session created: https://platform.claude.com/workspaces/%s/sessions/%s",
            workspace,
            session_id,
        )
        await self.client.beta.sessions.events.send(
            session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": self._kickoff(spec)}]}],
        )
        return RunHandle(run_id=spec.run_id, engine="cma", session_id=session_id)

    @staticmethod
    def _kickoff(spec: RunSpec) -> str:
        job_parts = []
        if spec.job_url:
            job_parts.append(f"Posting URL: {spec.job_url}")
        if spec.job_text:
            job_parts.append(f"\n### Posting text (verbatim)\n\n{spec.job_text}")
        return _KICKOFF_TEMPLATE.format(resume=spec.resume_text, job_section="\n".join(job_parts) or "(see URL)")

    async def events(self, h: RunHandle, cursor: str | None = None) -> AsyncIterator[WireEvent]:
        seen: dict[str, Any] = {}  # id -> last processed_at (re-yield on flip = upsert)
        terminal = False

        def fresh(d: dict[str, Any]) -> bool:
            eid = d.get("id")
            if not eid or not d.get("type"):
                return False  # stream bookkeeping frames (start/deltas) have no upsertable id
            marker = d.get("processed_at")
            if seen.get(eid, "\x00missing") == marker:
                return False
            seen[eid] = marker
            return True

        while not terminal:
            # 1. open the stream FIRST so no event falls between list and stream
            try:
                stream = await self.client.beta.sessions.events.stream(h.session_id)
            except Exception:
                logger.exception("CMA stream open failed (run %s); retrying", h.run_id)
                await asyncio.sleep(_RECONNECT_DELAY_S)
                continue
            try:
                # 2. reconcile: full list (asc, auto-paginated)
                async for ev in self.client.beta.sessions.events.list(h.session_id, order="asc"):
                    d = _event_to_dict(ev)
                    if fresh(d):
                        yield d
                        terminal = terminal or d.get("type") in _TERMINAL_TYPES
                if terminal:
                    break  # session already over — never block on a live tail (finally closes it)
                # 3. tail the live stream
                async for ev in stream:
                    d = _event_to_dict(ev)
                    if fresh(d):
                        yield d
                        if d.get("type") in _TERMINAL_TYPES:
                            terminal = True
                            break
            except Exception:
                logger.exception("CMA event stream dropped (run %s); reconsolidating", h.run_id)
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    try:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:  # noqa: BLE001 — best-effort close
                        pass
            if not terminal:
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def send_message(self, h: RunHandle, text: str) -> None:
        await self.client.beta.sessions.events.send(
            h.session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
        )

    async def answer(self, h: RunHandle, question_key: str, content: str) -> None:
        await self.client.beta.sessions.events.send(
            h.session_id,
            events=[
                {
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": question_key,
                    "content": [{"type": "text", "text": content}],
                }
            ],
        )

    async def interrupt(self, h: RunHandle) -> None:
        await self.client.beta.sessions.events.send(h.session_id, events=[{"type": "user.interrupt"}])

    async def usage(self, h: RunHandle) -> UsageNorm:
        session = await self.client.beta.sessions.retrieve(h.session_id)
        u = session["usage"] if isinstance(session, dict) else session.usage
        get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
        cache_creation = get("cache_creation")
        cache_creation_total = 0
        if cache_creation is not None:
            items = cache_creation.items() if isinstance(cache_creation, dict) else vars(cache_creation).items()
            cache_creation_total = sum(v for _, v in items if isinstance(v, int))
        input_tokens = (get("input_tokens") or 0) + (get("cache_read_input_tokens") or 0) + cache_creation_total
        output_tokens = get("output_tokens") or 0
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "usd": None,
        }

    # ── judge contract-file reads (§5: CMA research/gap from memory writes) ──

    async def read_contract_files(self) -> tuple[str | None, str | None]:
        """Best-effort latest applications/*/research.md + gap-analysis.md from
        the memory store. Callers fall back to '(none provided)' on None."""
        store_id = self._settings.cma_memory_store_id
        if not store_id:
            return None, None
        research: str | None = None
        gap: str | None = None
        best_research, best_gap = "", ""
        # live-verified 2026-07-13 (P0 smoke): path_prefix needs leading+trailing slash
        # (regex ^(/([^/\x00]+/)*)?$); depth is capped at 1 and depth=1 returns only
        async for item in self.client.beta.memory_stores.memories.list(
            store_id, path_prefix="/applications/", view="full"
        ):
            d = _event_to_dict(item)
            if d.get("type") != "memory":
                continue
            path, updated = str(d.get("path", "")), str(d.get("updated_at", ""))
            if path.endswith("/research.md") and updated >= best_research:
                research, best_research = d.get("content"), updated
            elif path.endswith("/gap-analysis.md") and updated >= best_gap:
                gap, best_gap = d.get("content"), updated
        return research, gap
