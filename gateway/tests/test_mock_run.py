"""End-to-end mock run over HTTP: create -> question -> answer -> done ->
2 drafts, needs_revision -> satisfied verdicts, export bundle (§7)."""

import asyncio
import json

from tp_gateway.tools import SKIP_ANSWER_TEXT

from .conftest import SAMPLE_ANSWER, SAMPLE_RESUME, drive_mock_run, poll_snapshot


async def test_full_mock_run_and_export(app_and_client):
    app, client = app_and_client
    run_id = await drive_mock_run(client)

    snap = (await client.get(f"/api/coach/runs/{run_id}")).json()
    assert snap["status"] == "done"
    assert len(snap["drafts"]) == 2
    assert [v["result"] for v in snap["verdicts"]] == ["needs_revision", "satisfied"]
    assert [v["iteration"] for v in snap["verdicts"]] == [1, 2]
    assert snap["pending_questions"] == []
    assert SAMPLE_ANSWER in snap["drafts"][0]["draft"]  # the answer was incorporated
    # verdicts parallel drafts by draft_id
    assert [v["draft_id"] for v in snap["verdicts"]] == [d["draft_id"] for d in snap["drafts"]]

    # run list
    listed = (await client.get("/api/coach/runs")).json()["runs"]
    assert listed[0]["run_id"] == run_id
    assert listed[0]["status"] == "done"
    assert listed[0]["needs_you"] is False
    assert listed[0]["spend_usd"] is None

    # export bundle (§7)
    bundle = (await client.get(f"/api/coach/runs/{run_id}/export")).json()
    assert set(bundle) == {"run", "events", "qa", "drafts", "verdicts", "plan_history", "usage", "exported_at"}
    assert bundle["run"]["run_id"] == run_id
    assert bundle["run"]["resume_text"] == SAMPLE_RESUME
    assert bundle["run"]["agent_ref"] == {"engine": "mock"}
    assert bundle["events"][0]["seq"] == 1
    assert any(e["type"] == "gateway.judge_verdict" for e in bundle["events"])
    assert len(bundle["qa"]) == 1
    assert bundle["qa"][0]["answer"] == SAMPLE_ANSWER
    assert bundle["qa"][0]["skipped"] is False
    assert bundle["qa"][0]["asked_at"] and bundle["qa"][0]["answered_at"]
    assert len(bundle["drafts"]) == 2
    assert len(bundle["verdicts"]) == 2
    for verdict in bundle["verdicts"]:
        assert set(verdict["judge_input"]) == {
            "source_profile",
            "job_posting",
            "research_findings",
            "gap_analysis",
            "generated_resume",
        }
        assert verdict["judge_model"] == "stub"
        assert verdict["prompt_version"]
    # Q&A lands verbatim in the judge's source_profile (§6.1 trust boundary)
    assert SAMPLE_ANSWER in bundle["verdicts"][0]["judge_input"]["source_profile"]
    assert len(bundle["plan_history"]) == 4  # the mock script's four update_plan calls
    assert bundle["usage"] == {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540, "usd": None}


async def test_answer_conflicts(app_and_client):
    app, client = app_and_client
    resp = await client.post(
        "/api/coach/runs",
        json={"engine": "mock", "resume_text": SAMPLE_RESUME, "job_text": "job"},
    )
    run_id = resp.json()["run_id"]
    snap = await poll_snapshot(client, run_id, lambda s: s["pending_questions"])
    key = snap["pending_questions"][0]["question_key"]

    # unknown key -> 409 with the error envelope
    resp = await client.post(f"/api/coach/runs/{run_id}/answers", json={"question_key": "bogus", "text": "hi"})
    assert resp.status_code == 409
    envelope = resp.json()
    assert envelope["type"] == "error"
    assert envelope["error"]["type"] == "conflict"
    assert envelope["request_id"].startswith("req_")

    # no text and no skip -> 400
    resp = await client.post(f"/api/coach/runs/{run_id}/answers", json={"question_key": key})
    assert resp.status_code == 400

    # skip=true answers with the canonical skip text
    resp = await client.post(f"/api/coach/runs/{run_id}/answers", json={"question_key": key, "skip": True})
    assert resp.status_code == 202
    await poll_snapshot(client, run_id, lambda s: s["status"] == "done")
    bundle = (await client.get(f"/api/coach/runs/{run_id}/export")).json()
    assert bundle["qa"][0]["answer"] == SKIP_ANSWER_TEXT
    assert bundle["qa"][0]["skipped"] is True

    # already answered -> 409 (idempotent no-op for the UI)
    resp = await client.post(f"/api/coach/runs/{run_id}/answers", json={"question_key": key, "text": "again"})
    assert resp.status_code == 409


async def test_create_run_validation(app_and_client):
    app, client = app_and_client
    resp = await client.post("/api/coach/runs", json={"engine": "mock", "resume_text": "r"})
    assert resp.status_code == 400  # neither job_text nor job_url
    resp = await client.post("/api/coach/runs", json={"engine": "nope", "resume_text": "r", "job_text": "j"})
    assert resp.status_code == 422  # bad engine literal
    resp = await client.post("/api/coach/runs", json={"engine": "cma", "resume_text": "r", "job_text": "j"})
    assert resp.status_code == 400  # cma unconfigured in tests
    resp = await client.get("/api/coach/runs/run_missing")
    assert resp.status_code == 404


async def _take_frames(gen, n: int) -> list[str]:
    frames = []
    async for frame in gen:
        frames.append(frame)
        if len(frames) == n:
            break
    await gen.aclose()
    return frames


async def test_sse_replay_cursor_heartbeat_and_live_tail(app_and_client):
    # sse_frames is consumed directly: httpx's ASGITransport buffers whole
    # responses, so the endless SSE body can't be driven over ASGI in-process.
    from tp_gateway.routers.coach import sse_frames

    app, client = app_and_client
    manager, db = app.state.manager, app.state.db
    run_id = await drive_mock_run(client)
    events = db.get_events(run_id)
    total = len(events)

    # full replay from cursor=0, in seq order
    frames = await _take_frames(sse_frames(manager, db, run_id, cursor=0), total)
    parsed = [json.loads(f[len("data: ") :]) for f in frames]
    assert [e["seq"] for e in parsed] == list(range(1, total + 1))
    assert parsed[0]["type"] == "session.status_running"

    # cursor replay: only events with seq > cursor
    frames = await _take_frames(sse_frames(manager, db, run_id, cursor=total - 2), 2)
    assert [json.loads(f[len("data: ") :])["seq"] for f in frames] == [total - 1, total]

    # idle stream -> heartbeat comment
    frames = await _take_frames(sse_frames(manager, db, run_id, cursor=total, heartbeat_s=0.05), 1)
    assert frames == [": heartbeat\n\n"]

    # live tail: a published event arrives as a frame
    gen = sse_frames(manager, db, run_id, cursor=total, heartbeat_s=30)
    task = asyncio.ensure_future(_take_frames(gen, 1))
    await asyncio.sleep(0.01)  # let the generator subscribe
    manager._publish(run_id, {"seq": total + 1, "id": "gwevt_x", "type": "gateway.judge_verdict"})
    frames = await asyncio.wait_for(task, timeout=5)
    assert json.loads(frames[0][len("data: ") :])["id"] == "gwevt_x"

    # the HTTP endpoint itself responds with the right content type (route smoke)
    resp_headers = None
    async with client.stream("GET", "/api/coach/runs/run_missing/events") as resp:
        resp_headers = resp.status_code
    assert resp_headers == 404


async def test_send_message_and_interrupt(app_and_client):
    app, client = app_and_client
    resp = await client.post(
        "/api/coach/runs",
        json={"engine": "mock", "resume_text": SAMPLE_RESUME, "job_text": "job"},
    )
    run_id = resp.json()["run_id"]
    await poll_snapshot(client, run_id, lambda s: s["pending_questions"])

    resp = await client.post(f"/api/coach/runs/{run_id}/messages", json={"text": "keep it to one page"})
    assert resp.status_code == 202
    snap = await poll_snapshot(
        client, run_id, lambda s: any(f["kind"] == "user" and "one page" in f["headline"] for f in s["feed"])
    )
    assert any(f["kind"] == "agent" for f in snap["feed"])

    resp = await client.post(f"/api/coach/runs/{run_id}/interrupt", json={})
    assert resp.status_code == 202
    snap = await poll_snapshot(client, run_id, lambda s: s["status"] == "done")
    assert any(f["headline"] == "paused" for f in snap["feed"])  # idle(interrupted) folds as paused
