"""Shared test harness: in-process app + the deterministic mock-run driver
that also generates the golden fixtures (see test_fold.py)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from tp_gateway.config import Settings
from tp_gateway.engines.mock_long import CANNED_ANSWERS, PERSONA_JOB, PERSONA_RESUME
from tp_gateway.fold import fold, snapshot_json
from tp_gateway.main import create_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_RUN_ID = "run_mock_fixture"
FIXTURE_TITLE = "Mock fixture run"
LONG_FIXTURE_RUN_ID = "run_mock_long_fixture"
LONG_FIXTURE_TITLE = "Mock long fixture run"

SAMPLE_RESUME = """Jordan Rivera — Backend Engineer

Six years building distributed services in Python and Go. Led the billing
pipeline migration to an event-driven architecture (p95 latency -40%).
Mentored two junior engineers; ran weekly production-readiness reviews.
Skills: Python, Go, Kubernetes, Terraform, PostgreSQL, Prometheus/Grafana."""

SAMPLE_JOB = """Site Reliability Engineer — Acme Corp

We need an engineer who owns incident response end-to-end, builds
observability into every service, and can harden a fast-moving platform.
Requirements: production incident ownership, IaC, Kubernetes at scale."""

SAMPLE_ANSWER = (
    "Yes — I was the de-facto on-call for our payments service for two years: "
    "I triaged incidents, wrote the postmortems, and built the runbook the team still uses."
)

# Snapshot.inputs (§2) for the golden fixture runs — the web-side golden fold
# test must pass the same inputs to its fold.
FIXTURE_INPUTS = {"resume_text": SAMPLE_RESUME, "job_text": SAMPLE_JOB, "job_url": None}
LONG_FIXTURE_INPUTS = {"resume_text": PERSONA_RESUME, "job_text": PERSONA_JOB, "job_url": None}


def make_settings(tmp_path: Path) -> Settings:
    # _env_file=None isolates tests from the owner's rendered .env;
    # tp_judge_stub="1" forces the stub even if OPENAI_API_KEY is exported.
    return Settings(
        tp_db_path=str(tmp_path / "gateway.db"),
        tp_mock_delay_ms=0,
        tp_judge_stub="1",
        _env_file=None,
    )


@pytest.fixture
async def app_and_client(tmp_path):
    app = create_app(make_settings(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield app, client
    await app.state.manager.shutdown()
    app.state.db.close()


async def poll_snapshot(client: httpx.AsyncClient, run_id: str, predicate, timeout_s: float = 10.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        snap = (await client.get(f"/api/coach/runs/{run_id}")).json()
        if predicate(snap):
            return snap
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timeout waiting for snapshot condition; last status={snap.get('status')}")
        await asyncio.sleep(0.01)


async def drive_mock_run(client: httpx.AsyncClient, title: str = "Mock run") -> str:
    """Create a mock run, answer the scripted question with SAMPLE_ANSWER,
    wait for done. Returns the run_id."""
    resp = await client.post(
        "/api/coach/runs",
        json={"engine": "mock", "title": title, "resume_text": SAMPLE_RESUME, "job_text": SAMPLE_JOB},
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["run_id"]
    snap = await poll_snapshot(client, run_id, lambda s: s["pending_questions"])
    key = snap["pending_questions"][0]["question_key"]
    resp = await client.post(f"/api/coach/runs/{run_id}/answers", json={"question_key": key, "text": SAMPLE_ANSWER})
    assert resp.status_code == 202, resp.text
    await poll_snapshot(client, run_id, lambda s: s["status"] == "done")
    return run_id


async def drive_mock_long_run(client: httpx.AsyncClient, title: str = "Mock long run") -> str:
    """Create a mock-long run, answer its four sequential questions with the
    CANNED_ANSWERS (choice / confirm / open / open), wait for done."""
    resp = await client.post(
        "/api/coach/runs",
        json={"engine": "mock-long", "title": title, "resume_text": PERSONA_RESUME, "job_text": PERSONA_JOB},
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["run_id"]
    answered: set[str] = set()
    for answer in CANNED_ANSWERS:
        snap = await poll_snapshot(
            client,
            run_id,
            lambda s: any(q["question_key"] not in answered for q in s["pending_questions"]),
            timeout_s=30.0,
        )
        key = next(q["question_key"] for q in snap["pending_questions"] if q["question_key"] not in answered)
        resp = await client.post(f"/api/coach/runs/{run_id}/answers", json={"question_key": key, "text": answer})
        assert resp.status_code == 202, resp.text
        answered.add(key)
    await poll_snapshot(client, run_id, lambda s: s["status"] == "done", timeout_s=30.0)
    return run_id


def _normalize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pin the only nondeterministic field (processed_at) to base+seq seconds so
    fixture regeneration is byte-stable. The fold never reads processed_at."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    for ev in events:
        ev = dict(ev)
        ev["processed_at"] = (base + timedelta(seconds=ev["seq"])).isoformat()
        out.append(ev)
    return out


async def _generate_fixture_pair(
    fixtures_dir: Path,
    tmp_path: Path,
    stem: str,
    drive,
    run_id: str,
    engine: str,
    title: str,
    inputs: dict,
) -> None:
    """Drive a scripted run against a fresh in-process app (delay 0, judge stub,
    canned answers applied) and write {stem}.jsonl + {stem}.snapshot.json."""
    app = create_app(make_settings(tmp_path))
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            live_run_id = await drive(client, title=title)
        events = _normalize_events(app.state.db.get_events(live_run_id))
    finally:
        await app.state.manager.shutdown()
        app.state.db.close()

    fixtures_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(ev, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for ev in events]
    (fixtures_dir / f"{stem}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    snapshot = fold(run_id, engine, title, events, inputs=inputs)
    (fixtures_dir / f"{stem}.snapshot.json").write_text(snapshot_json(snapshot), encoding="utf-8")


async def generate_fixtures(fixtures_dir: Path, tmp_path: Path) -> None:
    """Regenerate mock_run.jsonl + mock_run.snapshot.json (minimal scenario)."""
    await _generate_fixture_pair(
        fixtures_dir,
        tmp_path,
        "mock_run",
        drive_mock_run,
        FIXTURE_RUN_ID,
        "mock",
        FIXTURE_TITLE,
        FIXTURE_INPUTS,
    )


async def generate_long_fixtures(fixtures_dir: Path, tmp_path: Path) -> None:
    """Regenerate mock_run_long.jsonl + mock_run_long.snapshot.json (§8 mock-long)."""
    await _generate_fixture_pair(
        fixtures_dir,
        tmp_path,
        "mock_run_long",
        drive_mock_long_run,
        LONG_FIXTURE_RUN_ID,
        "mock-long",
        LONG_FIXTURE_TITLE,
        LONG_FIXTURE_INPUTS,
    )


# Golden fixture registry (test_fold.py parametrizes over it; the web repo's
# vitest golden test mirrors the same two pairs).
FIXTURE_CASES = {
    "mock_run": {
        "run_id": FIXTURE_RUN_ID,
        "engine": "mock",
        "title": FIXTURE_TITLE,
        "inputs": FIXTURE_INPUTS,
        "generate": generate_fixtures,
    },
    "mock_run_long": {
        "run_id": LONG_FIXTURE_RUN_ID,
        "engine": "mock-long",
        "title": LONG_FIXTURE_TITLE,
        "inputs": LONG_FIXTURE_INPUTS,
        "generate": generate_long_fixtures,
    },
}


async def ensure_fixture_pair(stem: str, tmp_path: Path) -> tuple[Path, Path]:
    """Return (jsonl, snapshot) paths for a fixture pair, generating if absent."""
    jsonl = FIXTURES_DIR / f"{stem}.jsonl"
    snapshot = FIXTURES_DIR / f"{stem}.snapshot.json"
    if not (jsonl.exists() and snapshot.exists()):
        await FIXTURE_CASES[stem]["generate"](FIXTURES_DIR, tmp_path)
    return jsonl, snapshot
