"""Golden fold test (CONTRACT.md §9) + fold edge rules.

The fixtures are GENERATED from the live mock engine (delay 0, judge stub,
sample answer applied) when absent — delete both files to regenerate after a
deliberate mock-script or fold change, and re-run the web-side vitest fold
against the same files.
"""

import json

import pytest

from tp_gateway.fold import KICKOFF_HEADLINE, fold, snapshot_json

from .conftest import FIXTURE_CASES, FIXTURE_INPUTS, ensure_fixture_pair


@pytest.mark.parametrize("stem", sorted(FIXTURE_CASES))
async def test_golden_fixture_fold(tmp_path, stem):
    """CONTRACT §9: BOTH fixture pairs (mock_run + mock_run_long) must fold to
    their pinned snapshots byte-identically (the TS fold pins the same files)."""
    case = FIXTURE_CASES[stem]
    jsonl, snapshot_file = await ensure_fixture_pair(stem, tmp_path)

    events = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    snapshot = fold(case["run_id"], case["engine"], case["title"], events, inputs=case["inputs"])
    assert snapshot_json(snapshot) == snapshot_file.read_text(encoding="utf-8")
    assert snapshot["status"] == "done"
    assert snapshot["engine"] == case["engine"]


async def test_golden_minimal_fixture_shape(tmp_path):
    jsonl, _ = await ensure_fixture_pair("mock_run", tmp_path)
    events = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    case = FIXTURE_CASES["mock_run"]
    snapshot = fold(case["run_id"], case["engine"], case["title"], events, inputs=case["inputs"])

    # sanity: the golden run's shape (guards against regenerating a broken fixture)
    assert snapshot["status"] == "done"
    assert snapshot["inputs"] == FIXTURE_INPUTS
    # kickoff feed rule (§3): first user.message folds to the inputs stub
    assert snapshot["feed"][0] == {"seq": 1, "kind": "user", "headline": KICKOFF_HEADLINE, "collapsed": True}
    # verdict explanations are user-facing — no agent imperative on the wire
    assert [v["explanation"] for v in snapshot["verdicts"]] == [
        "Grounding review found 1 finding(s).",
        "No grounding failures found.",
    ]
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
        _ev(i, "agent.message", content=[{"type": "text", "text": f"msg {i}"}]) for i in range(2, 43)
    ]  # 41 staleness-counted events > threshold of 40
    snap = fold("r", "cma", "t", [plan_ev, *chatter])
    assert snap["plan"]["stale"] is True

    # exactly at the threshold (40) -> not stale yet
    snap = fold("r", "cma", "t", [plan_ev, *chatter[:40]])
    assert snap["plan"]["stale"] is False

    # a fresh update_plan resets the counter
    plan_again = _ev(43, "agent.custom_tool_use", name="update_plan", input={
        "steps": [{"id": "s1", "title": "T", "status": "active"}], "current_step_id": "s1"})
    snap = fold("r", "cma", "t", [plan_ev, *chatter, plan_again])
    assert snap["plan"]["stale"] is False

    # no active step -> never stale
    plan_done = _ev(1, "agent.custom_tool_use", name="update_plan", input={
        "steps": [{"id": "s1", "title": "T", "status": "done"}], "current_step_id": None})
    snap = fold("r", "cma", "t", [plan_done, *chatter])
    assert snap["plan"]["stale"] is False


def test_plan_steps_tolerate_bare_strings_and_junk() -> None:
    """Live CMA agents sometimes emit bare step titles in update_plan (custom tools
    are not strict-validated). The fold degrades instead of crashing (500 on every
    snapshot) — found in production 2026-07-16."""
    events = [
        {"seq": 1, "id": "e1", "type": "agent.custom_tool_use", "processed_at": None,
         "name": "update_plan", "tool_use_id": "t1",
         "input": {"steps": ["research the role", {"id": "a", "title": "draft", "status": "active"}, 42, None]}},
    ]
    snap = fold("r", "mock", "t", events)
    assert snap["plan"]["steps"] == [
        {"id": "research the role", "title": "research the role", "status": "pending"},
        {"id": "a", "title": "draft", "status": "active"},
    ]


def _plan_ev(steps, current: str | None = None) -> dict:
    return _ev(1, "agent.custom_tool_use", name="update_plan",
               input={"steps": steps, "current_step_id": current})


def test_plan_steps_json_encoded_string_recovers_the_plan() -> None:
    """A REAL CMA run emitted steps as a JSON-ENCODED STRING; iterating its
    characters exploded into hundreds of one-char steps. §3: exactly ONE
    json.loads attempt — an array result is used."""
    steps = [
        {"id": "research", "title": "Research the company", "status": "done"},
        {"id": "draft", "title": "Draft the resume", "status": "active"},
        "polish",  # bare-string ITEM coercion still applies to parsed items
    ]
    snap = fold("r", "cma", "t", [_plan_ev(json.dumps(steps), current="draft")])
    assert snap["plan"]["steps"] == [
        {"id": "research", "title": "Research the company", "status": "done"},
        {"id": "draft", "title": "Draft the resume", "status": "active"},
        {"id": "polish", "title": "polish", "status": "pending"},
    ]
    assert snap["plan"]["current_step_id"] == "draft"


def test_plan_steps_garbage_string_folds_to_empty() -> None:
    # unparseable string -> no steps, NEVER per-character steps
    snap = fold("r", "cma", "t", [_plan_ev("research, then draft, then polish")])
    assert snap["plan"]["steps"] == []
    # a string that parses to a non-array (double-encoded scalar / object) -> no steps
    snap = fold("r", "cma", "t", [_plan_ev('"just a title"')])
    assert snap["plan"]["steps"] == []
    snap = fold("r", "cma", "t", [_plan_ev('{"id": "a", "title": "T"}')])
    assert snap["plan"]["steps"] == []
    # non-string, non-list steps payloads also fold to no steps
    snap = fold("r", "cma", "t", [_plan_ev({"id": "a"})])
    assert snap["plan"]["steps"] == []


def test_plan_duplicate_ids_deduplicated_deterministically() -> None:
    """First occurrence keeps its id; later duplicates get #2, #3… (count per
    base id); current_step_id refers to the first occurrence."""
    steps = [
        {"id": "step", "title": "A", "status": "done"},
        {"id": "step", "title": "B", "status": "active"},
        {"id": "other", "title": "C", "status": "pending"},
        {"id": "step", "title": "D", "status": "pending"},
        "step",  # bare-string coercion feeds the same dedupe
    ]
    snap = fold("r", "cma", "t", [_plan_ev(steps, current="step")])
    assert [s["id"] for s in snap["plan"]["steps"]] == ["step", "step#2", "other", "step#3", "step#4"]
    assert [s["title"] for s in snap["plan"]["steps"]] == ["A", "B", "C", "D", "step"]
    assert snap["plan"]["current_step_id"] == "step"  # first occurrence keeps the id


def test_kickoff_feed_rule_first_user_message_only() -> None:
    """§3: the run's FIRST user.message folds to the collapsed inputs stub with
    NO body; later user messages fold normally."""
    events = [
        _ev(1, "user.message", content=[{"type": "text", "text": "## RESUME\n\nlots of blob text\n\n## JOB\n\nmore blob"}]),
        _ev(2, "session.status_running"),
        _ev(3, "user.message", content=[{"type": "text", "text": "keep it to one page\nand skip the objective"}]),
    ]
    snap = fold("r", "cma", "t", events)
    assert snap["feed"][0] == {"seq": 1, "kind": "user", "headline": KICKOFF_HEADLINE, "collapsed": True}
    assert "body" not in snap["feed"][0]
    assert snap["feed"][1] == {
        "seq": 3, "kind": "user", "headline": "keep it to one page",
        "body": "and skip the objective", "collapsed": False,
    }


def test_snapshot_inputs_defaults_empty_and_passes_through() -> None:
    snap = fold("r", "cma", "t", [])
    assert snap["inputs"] == {"resume_text": "", "job_text": "", "job_url": None}
    inputs = {"resume_text": "R", "job_text": "J", "job_url": "https://jobs.example/x"}
    snap = fold("r", "cma", "t", [], inputs=inputs)
    assert snap["inputs"] == inputs
    # inputs sits right after title (contract §2 field order on the HTTP wire)
    assert list(snap.keys())[:5] == ["run_id", "engine", "title", "inputs", "status"]


def test_plan_steps_recover_from_tool_call_artifact_wrapper() -> None:
    """Real payload shape from live CMA run run_7b5a0a8a4501 (2026-07-16): the steps
    array arrived wrapped in a tool-call artifact. Bracket-extraction recovers it."""
    from tp_gateway.fold import fold

    wrapped = '\n<parameter name="steps">[{"id":"mem","title":"Read memory","status":"done"},{"id":"draft","title":"Draft","status":"active"}]'
    events = [
        {"seq": 1, "id": "e1", "type": "agent.custom_tool_use", "processed_at": None,
         "name": "update_plan", "tool_use_id": "t1", "input": {"steps": wrapped}},
    ]
    snap = fold("r", "cma", "t", events)
    assert [s["id"] for s in snap["plan"]["steps"]] == ["mem", "draft"]
    assert snap["plan"]["steps"][1]["status"] == "active"


def test_plan_steps_wrapper_with_garbage_still_empty() -> None:
    from tp_gateway.fold import fold

    events = [
        {"seq": 1, "id": "e1", "type": "agent.custom_tool_use", "processed_at": None,
         "name": "update_plan", "tool_use_id": "t1", "input": {"steps": "<p>[not json]</p>"}},
    ]
    snap = fold("r", "cma", "t", events)
    assert snap["plan"]["steps"] == []


def test_legacy_verdict_explanation_suffix_stripped() -> None:
    """Verdicts stored before 2026-07-17 carry the agent-facing imperative — display strips it."""
    from tp_gateway.fold import fold

    events = [
        {"seq": 1, "id": "v1", "type": "gateway.judge_verdict", "processed_at": None,
         "draft_id": "d1", "result": "needs_revision", "iteration": 1,
         "explanation": "Grounding review found 1 finding(s); address each and resubmit.",
         "findings": [], "rubric": None},
    ]
    snap = fold("r", "cma", "t", events)
    assert snap["verdicts"][0]["explanation"] == "Grounding review found 1 finding(s)."
    assert snap["feed"][-1]["body"] == "Grounding review found 1 finding(s)."
