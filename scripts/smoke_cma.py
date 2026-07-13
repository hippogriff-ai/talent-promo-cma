#!/usr/bin/env python
"""P0 live smoke against real CMA (spec §8 P0 gate). Run: make smoke-cma

Drives the CmaEngine adapter DIRECTLY (no HTTP gateway needed): creates a real
session, tails consolidated events, auto-acks update_plan, prompts YOU on
stdin for every ask_user, judges submit_draft (stub unless OPENAI_API_KEY is
set), and prints the final drafts + spend.

Reads the repo-root .env via tp_gateway.config.Settings — needs
ANTHROPIC_API_KEY + CMA_AGENT_ID + CMA_ENVIRONMENT_ID (infra/cma/setup.sh).

Optional args:
    --resume-file PATH   resume text file (default: built-in sample)
    --job-url URL        job posting URL
    --job-file PATH      job posting text file
    --title TITLE        session title
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import logging

from tp_gateway.config import Settings
from tp_gateway.engines.base import RunSpec
from tp_gateway.engines.cma import CmaEngine
from tp_gateway.tools import judge_draft, render_judge_input, verdict_tool_result

# Opus 4.8 list price (spec §5.8) — smoke-report estimate only, not billing truth.
_USD_PER_MTOK_IN = 5.0
_USD_PER_MTOK_OUT = 25.0

SAMPLE_RESUME = """Jordan Rivera — Backend Engineer

Six years building distributed services in Python and Go. Led the billing
pipeline migration to an event-driven architecture (p95 latency -40%).
Mentored two junior engineers; ran weekly production-readiness reviews.
Skills: Python, Go, Kubernetes, Terraform, PostgreSQL, Prometheus/Grafana."""

SAMPLE_JOB = """Site Reliability Engineer — Acme Corp

We need an engineer who owns incident response end-to-end, builds
observability into every service, and can harden a fast-moving platform.
Requirements: production incident ownership, IaC, Kubernetes at scale."""


def _summarize(ev: dict) -> str:
    t = ev.get("type", "?")
    if t in ("agent.message", "user.message"):
        text = " ".join(b.get("text", "") for b in ev.get("content") or [] if b.get("type") == "text")
        return f"{t}: {text[:160]}"
    if t in ("agent.tool_use", "agent.mcp_tool_use", "agent.custom_tool_use"):
        return f"{t}: {ev.get('name')} {json.dumps(ev.get('input') or {}, ensure_ascii=False)[:140]}"
    if t == "session.status_idle":
        return f"{t}: {json.dumps(ev.get('stop_reason') or {}, ensure_ascii=False)}"
    if t == "session.error":
        return f"{t}: {json.dumps(ev.get('error') or {}, ensure_ascii=False)}"
    return t


async def _ask_stdin(question: str, context: str | None) -> str:
    print("\n" + "=" * 72)
    print("COACH ASKS:")
    print(f"  {question}")
    if context:
        print(f"  (why: {context})")
    print("=" * 72)
    answer = await asyncio.to_thread(input, "your answer (empty = skip)> ")
    return answer.strip() or "[skipped — the candidate chose not to answer; move on]"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-file", type=Path)
    parser.add_argument("--job-url")
    parser.add_argument("--job-file", type=Path)
    parser.add_argument("--title", default="smoke-cma P0")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    missing = [
        name
        for name, val in (
            ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
            ("CMA_AGENT_ID", settings.cma_agent_id),
            ("CMA_ENVIRONMENT_ID", settings.cma_environment_id),
        )
        if not val
    ]
    if missing:
        print(f"missing from .env: {', '.join(missing)} — run `make secrets` + infra/cma/setup.sh")
        return 1
    if settings.judge_stub_enabled:
        print("note: OPENAI_API_KEY unset (or TP_JUDGE_STUB=1) — judge runs in deterministic stub mode")

    resume_text = args.resume_file.read_text() if args.resume_file else SAMPLE_RESUME
    job_text = args.job_file.read_text() if args.job_file else ("" if args.job_url else SAMPLE_JOB)

    engine = CmaEngine(settings)
    spec = RunSpec(
        run_id="smoke_" + uuid.uuid4().hex[:8],
        title=args.title,
        resume_text=resume_text,
        job_text=job_text,
        job_url=args.job_url,
    )
    handle = await engine.create_run(spec)
    print(f"session: {handle.session_id}")

    tool_uses: dict[str, dict] = {}  # event id -> {name, input}
    qa_rows: list[dict] = []
    drafts: list[dict] = []
    iteration = 0

    async for ev in engine.events(handle):
        print(f"  {_summarize(ev)}")
        t = ev.get("type")
        if t in ("agent.custom_tool_use", "agent.tool_use", "agent.mcp_tool_use") and ev.get("name") in (
            "update_plan",
            "ask_user",
            "submit_draft",
        ):
            tool_uses[ev["id"]] = {"name": ev["name"], "input": ev.get("input") or {}}
        elif t == "session.status_idle":
            stop = ev.get("stop_reason") or {}
            if stop.get("type") == "end_turn":
                break
            if stop.get("type") != "requires_action":
                continue
            for eid in stop.get("event_ids") or []:
                use = tool_uses.get(eid)
                if use is None:
                    print(f"  !! idle blocked on unknown event {eid}; skipping")
                    continue
                name, tool_input = use["name"], use["input"]
                if name == "update_plan":
                    await engine.answer(handle, eid, "ok")
                elif name == "ask_user":
                    answer = await _ask_stdin(tool_input.get("question", "?"), tool_input.get("context"))
                    qa_rows.append({"question": tool_input.get("question", ""), "answer": answer})
                    await engine.answer(handle, eid, answer)
                elif name == "submit_draft":
                    draft_text = tool_input.get("draft") or tool_input.get("text") or ""
                    drafts.append({"label": tool_input.get("label") or "draft", "draft": draft_text})
                    research, gap = None, None
                    try:
                        research, gap = await engine.read_contract_files()
                    except Exception as exc:  # noqa: BLE001 — best-effort memory read
                        print(f"  !! memory contract-file read failed: {exc}")
                    judge_input = render_judge_input(
                        resume_text,
                        qa_rows_with_answers(qa_rows),
                        job_text,
                        tool_input.get("research_notes") or research,
                        tool_input.get("gap_analysis") or gap,
                        draft_text,
                    )
                    iteration += 1
                    verdict = await judge_draft(settings, judge_input, iteration)
                    print(f"  judge[{verdict.judge_model}] -> {verdict.result}: {verdict.explanation}")
                    for f in verdict.findings:
                        print(f"    - [{f['severity']}] {f['failure_mode']}: {f['span'][:100]}")
                    await engine.answer(
                        handle,
                        eid,
                        verdict_tool_result(verdict.result, verdict.explanation, verdict.findings, verdict.rubric),
                    )
        elif t in ("session.status_terminated", "session.deleted"):
            break

    print("\n" + "#" * 72)
    print(f"FINAL DRAFTS ({len(drafts)}):")
    for d in drafts:
        print(f"\n--- {d['label']} " + "-" * 40)
        print(d["draft"])
    usage = await engine.usage(handle)
    est = usage["input_tokens"] / 1e6 * _USD_PER_MTOK_IN + usage["output_tokens"] / 1e6 * _USD_PER_MTOK_OUT
    print("\nSPEND:")
    print(f"  tokens: {usage['input_tokens']} in / {usage['output_tokens']} out = {usage['total_tokens']}")
    print(f"  est. ${est:.2f} (Opus 4.8 list price; Console is billing truth)")
    return 0


def qa_rows_with_answers(qa_rows: list[dict]) -> list[dict]:
    return [{"question": q["question"], "answer": q["answer"]} for q in qa_rows]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
