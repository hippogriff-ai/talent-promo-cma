"""Golden fold test (CONTRACT.md §9) + fold edge rules.

The fixtures are GENERATED from the live mock engine (delay 0, judge stub,
sample answer applied) when absent — delete both files to regenerate after a
deliberate mock-script or fold change, and re-run the web-side vitest fold
against the same files.
"""

import json

from tp_gateway.fold import fold, snapshot_json

from .conftest import FIXTURE_RUN_ID, FIXTURE_TITLE, FIXTURES_DIR, generate_fixtures


async def test_golden_fixture_fold(tmp_path):
    jsonl = FIXTURES_DIR / "mock_run.jsonl"
    snapshot_file = FIXTURES_DIR / "mock_run.snapshot.json"
    if not (jsonl.exists() and snapshot_file.exists()):
        await generate_fixtures(FIXTURES_DIR, tmp_path)

    events = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    snapshot = fold(FIXTURE_RUN_ID, "mock", FIXTURE_TITLE, events)
    assert snapshot_json(snapshot) == snapshot_file.read_text(encoding="utf-8")

    # sanity: the golden run's shape (guards against regenerating a broken fixture)
    assert snapshot["status"] == "done"
    assert len(snapshot["drafts"]) == 2
    assert [v["result"] for v in snapshot["verdicts"]] == ["needs_revision", "satisfied"]
    assert snapshot["pending_questions"] == []
    assert snapshot["plan"] is not None
    assert all(s["status"] == "done" for s in snapshot["plan"]["steps"])
    assert snapshot["usage"]["input_tokens"] == 1200
    assert snapshot["usage"]["output_tokens"] == 340
    assert snapshot["usage"]["usd"] is None


def _ev(seq: int, type_: str, **payload) -> dict:
    return {"seq": seq, "id": f"evt_{seq:03d}", "type": type_, "processed_at": None, **payload}


def test_unknown_types_ignored_and_escalation_whitelisted():
    events = [
        _ev(1, "session.status_running"),
        _ev(2, "some.future_type", data={"x": 1}),
        _ev(3, "reve.escalation", reason="draft held below floor"),
    ]
    snap = fold("r", "mock", "t", events)
    assert len(snap["feed"]) == 1
    assert snap["feed"][0]["headline"] == "reve escalation: draft held below floor"
    assert snap["cursor"] == 3


def test_upsert_by_id_replaces_not_duplicates():
    ask = _ev(2, "agent.custom_tool_use", name="ask_user", input={"question": "Q1?"})
    flipped = {**ask, "seq": 5, "processed_at": "2026-01-01T00:00:05+00:00"}
    events = [_ev(1, "session.status_running"), ask, flipped]
    snap = fold("r", "mock", "t", events)
    assert len(snap["pending_questions"]) == 1
    assert snap["pending_questions"][0]["asked_seq"] == 2  # first arrival's seq kept


def test_needs_you_derives_from_outstanding_questions():
    # idle(requires_action) with NO pending ask_user (e.g. submit_draft block) stays working
    draft = _ev(2, "agent.custom_tool_use", name="submit_draft", input={"draft": "text"})
    idle = _ev(3, "session.status_idle", stop_reason={"type": "requires_action", "event_ids": [draft["id"]]})
    snap = fold("r", "mock", "t", [_ev(1, "session.status_running"), draft, idle])
    assert snap["status"] == "working"
    assert len(snap["drafts"]) == 1

    ask = _ev(4, "agent.custom_tool_use", name="ask_user", input={"question": "Q?"})
    idle2 = _ev(5, "session.status_idle", stop_reason={"type": "requires_action", "event_ids": [ask["id"]]})
    snap = fold("r", "mock", "t", [_ev(1, "session.status_running"), draft, idle, ask, idle2])
    assert snap["status"] == "needs_you"


def test_custom_tools_fold_from_plain_tool_use_too():
    # engine-agnostic rule: reve hands arrive as agent.tool_use with the custom names
    events = [
        _ev(1, "agent.tool_use", name="update_plan", input={"steps": [{"id": "s1", "title": "T", "status": "active"}]}),
        _ev(2, "agent.tool_use", name="submit_draft", input={"text": "fallback body"}, tool_use_id="toolu_1"),
    ]
    snap = fold("r", "cma", "t", events)
    assert snap["plan"]["steps"][0]["id"] == "s1"
    assert snap["drafts"][0]["draft"] == "fallback body"  # input.text fallback
    assert snap["drafts"][0]["draft_id"] == "toolu_1"  # tool_use_id preferred over event id
    assert snap["feed"] == []  # custom tools are not tool feed items


def test_unknown_stop_reason_pauses_and_terminated_fails():
    events = [
        _ev(1, "session.status_running"),
        _ev(2, "session.status_idle", stop_reason={"type": "budget_exhausted"}),
    ]
    snap = fold("r", "cma", "t", events)
    assert snap["status"] == "working"
    assert snap["feed"][-1]["headline"] == "paused"

    events.append(_ev(3, "session.status_terminated"))
    snap = fold("r", "cma", "t", events)
    assert snap["status"] == "failed"


def test_done_survives_terminated():
    events = [
        _ev(1, "session.status_idle", stop_reason={"type": "end_turn"}),
        _ev(2, "session.status_terminated"),
    ]
    assert fold("r", "cma", "t", events)["status"] == "done"


def test_usage_accumulates_cache_tokens():
    events = [
        _ev(
            1,
            "span.model_request_end",
            model_usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 20,
            },
        ),
        _ev(2, "span.model_request_end", model_usage={"input_tokens": 10, "output_tokens": 5}),
    ]
    usage = fold("r", "cma", "t", events)["usage"]
    assert usage == {"input_tokens": 160, "output_tokens": 55, "total_tokens": 215, "usd": None}


def test_plan_staleness():
    plan_ev = _ev(1, "agent.custom_tool_use", name="update_plan", input={
        "steps": [{"id": "s1", "title": "T", "status": "active"}], "current_step_id": "s1"})
    chatter = [
        _ev(i, "agent.message", content=[{"type": "text", "text": f"msg {i}"}]) for i in range(2, 18)
    ]  # 16 staleness-counted events > threshold of 15
    snap = fold("r", "cma", "t", [plan_ev, *chatter])
    assert snap["plan"]["stale"] is True

    # a fresh update_plan resets the counter
    plan_again = _ev(18, "agent.custom_tool_use", name="update_plan", input={
        "steps": [{"id": "s1", "title": "T", "status": "active"}], "current_step_id": "s1"})
    snap = fold("r", "cma", "t", [plan_ev, *chatter, plan_again])
    assert snap["plan"]["stale"] is False

    # no active step -> never stale
    plan_done = _ev(1, "agent.custom_tool_use", name="update_plan", input={
        "steps": [{"id": "s1", "title": "T", "status": "done"}], "current_step_id": None})
    snap = fold("r", "cma", "t", [plan_done, *chatter])
    assert snap["plan"]["stale"] is False
