"""CmaEngine consolidation + wire-payload tests against a fake SDK client
(CMA cannot be live-tested here — CONTRACT.md §6)."""

import asyncio
from datetime import datetime, timezone

import pytest

import tp_gateway.engines.cma as cma_module
from tp_gateway.db import Database
from tp_gateway.engines.base import RunHandle, RunSpec
from tp_gateway.engines.cma import CmaEngine
from tp_gateway.relay import RunManager



def cma_settings(tmp_path):
    from tp_gateway.config import Settings

    return Settings(
        tp_db_path=str(tmp_path / "gateway.db"),
        tp_mock_delay_ms=0,
        tp_judge_stub="1",
        anthropic_api_key="key",
        cma_agent_id="agent_1",
        cma_agent_version=3,
        cma_environment_id="env_1",
        cma_memory_store_id="memstore_1",
        cma_workspace_id="wrkspc_1",
        _env_file=None,
    )


# ── fake SDK surface ─────────────────────────────────────────────────────────


class FakeStream:
    def __init__(self, events, drop_after: bool = False):
        self._events = list(events)
        self._drop = drop_after
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for ev in self._events:
            yield ev
        if self._drop:
            raise ConnectionError("stream dropped")

    async def close(self):
        self.closed = True


class FakeEventsAPI:
    def __init__(self, streams=None, list_snapshots=None):
        self.streams = list(streams or [])
        self.list_snapshots = list(list_snapshots or [[]])
        self.sent: list[tuple[str, list]] = []
        self._stream_i = 0
        self._list_i = 0

    async def stream(self, session_id, **kwargs):
        i = min(self._stream_i, len(self.streams) - 1)
        self._stream_i += 1
        return self.streams[i]

    def list(self, session_id, **kwargs):
        i = min(self._list_i, len(self.list_snapshots) - 1)
        self._list_i += 1
        snapshot = self.list_snapshots[i]

        async def gen():
            for ev in snapshot:
                yield ev

        return gen()

    async def send(self, session_id, events, **kwargs):
        self.sent.append((session_id, list(events)))


class FakeMemoriesAPI:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.calls: list[dict] = []

    def list(self, memory_store_id, **kwargs):
        self.calls.append({"memory_store_id": memory_store_id, **kwargs})
        items = self.items

        async def gen():
            for it in items:
                yield it

        return gen()


class FakeSessionsAPI:
    def __init__(self, events_api):
        self.events = events_api
        self.created: list[dict] = []
        self.usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 2,
            "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 0},
        }

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "sess_fake", "type": "session"}

    async def retrieve(self, session_id, **kwargs):
        return {"id": session_id, "usage": self.usage}


class FakeClient:
    def __init__(self, events_api=None, memories_api=None):
        events_api = events_api or FakeEventsAPI()
        sessions = FakeSessionsAPI(events_api)
        memory_stores = type("MS", (), {"memories": memories_api or FakeMemoriesAPI()})()
        self.beta = type("Beta", (), {"sessions": sessions, "memory_stores": memory_stores})()


def _ev(eid: str, type_: str, processed_at: str = "t1", **payload) -> dict:
    return {"id": eid, "type": type_, "processed_at": processed_at, **payload}


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(cma_module, "_RECONNECT_DELAY_S", 0)


HANDLE = RunHandle(run_id="run_x", engine="cma", session_id="sess_fake")


async def collect(engine: CmaEngine) -> list[dict]:
    return [ev async for ev in engine.events(HANDLE)]


# ── consolidation ────────────────────────────────────────────────────────────


async def test_stream_first_list_reconcile_dedupe_and_upsert(tmp_path):
    a1, b1 = _ev("A", "agent.message"), _ev("B", "agent.thinking")
    a2 = _ev("A", "agent.message", processed_at="t2")  # processed_at flip -> re-yield (upsert)
    c1, d1 = _ev("C", "agent.message"), _ev("D", "agent.message")
    term = _ev("TERM", "session.status_terminated")

    events_api = FakeEventsAPI(
        streams=[
            FakeStream([b1, a2, c1], drop_after=True),  # B is a dup of the list; A flips; then drop
            FakeStream([_ev("E", "agent.message"), term]),
        ],
        list_snapshots=[
            [a1, b1],  # reconcile pass 1
            [a2, b1, c1, d1],  # reconcile pass 2 after the drop: only D is new (A stays at t2)
        ],
    )
    engine = CmaEngine(cma_settings(tmp_path), client=FakeClient(events_api))
    got = await asyncio.wait_for(collect(engine), timeout=5)
    assert [(e["id"], e["processed_at"]) for e in got] == [
        ("A", "t1"),
        ("B", "t1"),
        ("A", "t2"),  # flip re-delivered, never dropped
        ("C", "t1"),
        ("D", "t1"),
        ("E", "t1"),
        ("TERM", "t1"),
    ]
    assert events_api.streams[0].closed and events_api.streams[1].closed


async def test_frames_without_id_are_skipped(tmp_path):
    term = _ev("TERM", "session.status_terminated")
    events_api = FakeEventsAPI(streams=[FakeStream([{"type": "start"}, term])])
    engine = CmaEngine(cma_settings(tmp_path), client=FakeClient(events_api))
    got = await asyncio.wait_for(collect(engine), timeout=5)
    assert [e["id"] for e in got] == ["TERM"]


# ── create / send payload shapes ─────────────────────────────────────────────


async def test_create_run_session_and_kickoff(tmp_path):
    client = FakeClient()
    engine = CmaEngine(cma_settings(tmp_path), client=client)
    spec = RunSpec(
        run_id="run_x", title="Acme — SRE", resume_text="MY RESUME",
        job_text="THE JOB", job_url="https://jobs.acme.example/sre",
    )
    handle = await engine.create_run(spec)
    assert handle.session_id == "sess_fake"

    created = client.beta.sessions.created[0]
    assert created["agent"] == {"type": "agent", "id": "agent_1", "version": 3}
    assert created["environment_id"] == "env_1"
    assert created["metadata"] == {"run_id": "run_x", "engine": "cma"}
    assert created["title"] == "Acme — SRE"
    (resource,) = created["resources"]
    assert resource["type"] == "memory_store"
    assert resource["memory_store_id"] == "memstore_1"
    assert resource["access"] == "read_write"

    session_id, sent_events = client.beta.sessions.events.sent[0]
    assert session_id == "sess_fake"
    (kickoff,) = sent_events
    assert kickoff["type"] == "user.message"
    text = kickoff["content"][0]["text"]
    assert "MY RESUME" in text and "THE JOB" in text and "https://jobs.acme.example/sre" in text


async def test_answer_interrupt_and_message_payloads(tmp_path):
    client = FakeClient()
    engine = CmaEngine(cma_settings(tmp_path), client=client)

    await engine.answer(HANDLE, "sevt_ask1", "my answer")
    await engine.send_message(HANDLE, "steer left")
    await engine.interrupt(HANDLE)

    sent = client.beta.sessions.events.sent
    assert sent[0][1] == [
        {
            "type": "user.custom_tool_result",
            "custom_tool_use_id": "sevt_ask1",
            "content": [{"type": "text", "text": "my answer"}],
        }
    ]
    assert sent[1][1] == [{"type": "user.message", "content": [{"type": "text", "text": "steer left"}]}]
    assert sent[2][1] == [{"type": "user.interrupt"}]


async def test_usage_normalization(tmp_path):
    engine = CmaEngine(cma_settings(tmp_path), client=FakeClient())
    usage = await engine.usage(HANDLE)
    # 10 input + 2 cache_read + 3 cache_creation = 15
    assert usage == {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20, "usd": None}


async def test_read_contract_files_picks_latest(tmp_path):
    memories = FakeMemoriesAPI(
        items=[
            {"type": "memory_prefix", "path": "/applications/acme-sre/"},
            {
                "type": "memory", "id": "mem_1", "path": "/applications/acme-sre/research.md",
                "updated_at": "2026-07-13T01:00:00Z", "content": "OLD RESEARCH",
            },
            {
                "type": "memory", "id": "mem_2", "path": "/applications/acme-sre/research.md",
                "updated_at": "2026-07-13T02:00:00Z", "content": "NEW RESEARCH",
            },
            {
                "type": "memory", "id": "mem_3", "path": "/applications/acme-sre/gap-analysis.md",
                "updated_at": "2026-07-13T01:30:00Z", "content": "GAPS",
            },
        ]
    )
    engine = CmaEngine(cma_settings(tmp_path), client=FakeClient(memories_api=memories))
    research, gap = await engine.read_contract_files()
    assert research == "NEW RESEARCH"
    assert gap == "GAPS"
    assert memories.calls[0]["path_prefix"] == "/applications/"


# ── idle-dispatch through the relay, driven by the fake CMA client ───────────


async def test_relay_dispatch_from_fake_cma(tmp_path):
    plan_use = _ev(
        "sevt_plan", "agent.custom_tool_use",
        name="update_plan", input={"steps": [{"id": "s1", "title": "T", "status": "active"}]},
    )
    idle = _ev(
        "sevt_idle", "session.status_idle",
        stop_reason={"type": "requires_action", "event_ids": ["sevt_plan"]},
    )
    term = _ev("sevt_term", "session.status_terminated")
    events_api = FakeEventsAPI(streams=[FakeStream([plan_use, idle, term])])
    settings = cma_settings(tmp_path)
    db = Database(settings.db_path)
    engine = CmaEngine(settings, client=FakeClient(events_api))
    manager = RunManager(settings, db, cma_engine=engine)
    db.insert_run(
        "run_x", "cma", "T", "resume", "job", None, {"engine": "cma"},
        datetime.now(timezone.utc).isoformat(),
    )
    db.set_run_session("run_x", "sess_fake")

    await asyncio.wait_for(manager._relay("run_x", HANDLE), timeout=5)

    # events landed in SQLite with gateway seqs
    assert [e["id"] for e in db.get_events("run_x")] == ["sevt_plan", "sevt_idle", "sevt_term"]
    assert len(db.list_plans("run_x")) == 1
    # the blocking update_plan was auto-acked "ok" over the wire
    acks = [e for _, evs in events_api.sent for e in evs if e["type"] == "user.custom_tool_result"]
    assert acks == [
        {
            "type": "user.custom_tool_result",
            "custom_tool_use_id": "sevt_plan",
            "content": [{"type": "text", "text": "ok"}],
        }
    ]
