"""Custom-tool dispatch (§4) + judge stub/rendering (§5) unit tests, driven
through RunManager._react with a recording fake adapter."""

import json
from datetime import datetime, timezone

from tp_gateway.db import Database
from tp_gateway.engines.base import RunHandle
from tp_gateway.judge import JudgeInput
from tp_gateway.relay import RunManager
from tp_gateway.tools import (
    JUDGE_INSTRUCTION,
    NONE_PROVIDED,
    judge_draft,
    render_judge_input,
    stub_findings,
)

from .conftest import make_settings

RUN_ID = "run_test"


class FakeAdapter:
    def __init__(self):
        self.answers: list[tuple[str, str]] = []

    async def answer(self, h, key, content):
        self.answers.append((key, content))


def make_manager(tmp_path) -> tuple[RunManager, Database, RunHandle, FakeAdapter]:
    settings = make_settings(tmp_path)
    db = Database(settings.db_path)
    manager = RunManager(settings, db)
    db.insert_run(
        RUN_ID, "mock", "T", "resume text", "job text", None, {"engine": "mock"},
        datetime.now(timezone.utc).isoformat(),
    )
    return manager, db, RunHandle(run_id=RUN_ID, engine="mock", session_id=RUN_ID), FakeAdapter()


async def feed_event(manager: RunManager, adapter: FakeAdapter, handle: RunHandle, event: dict) -> dict:
    """Mimic one relay iteration: upsert then react."""
    seq, _ = manager.db.upsert_event(RUN_ID, event)
    wire = {"seq": seq, **event}
    await manager._react(RUN_ID, handle, adapter, wire)
    return wire


def idle(eid: str, *blocked_on: str) -> dict:
    return {
        "id": eid,
        "type": "session.status_idle",
        "processed_at": None,
        "stop_reason": {"type": "requires_action", "event_ids": list(blocked_on)},
    }


async def test_update_plan_auto_ack(tmp_path):
    manager, db, handle, adapter = make_manager(tmp_path)
    plan_ev = {
        "id": "evt_plan", "type": "agent.custom_tool_use", "processed_at": None,
        "name": "update_plan",
        "input": {"steps": [{"id": "s1", "title": "T", "status": "active"}], "current_step_id": "s1"},
    }
    await feed_event(manager, adapter, handle, plan_ev)
    plans = db.list_plans(RUN_ID)
    assert len(plans) == 1 and plans[0]["current_step_id"] == "s1"
    assert adapter.answers == []  # persisted on arrival, acked only at idle

    await feed_event(manager, adapter, handle, idle("evt_idle1", "evt_plan"))
    assert adapter.answers == [("evt_plan", "ok")]


async def test_ask_user_persists_and_holds(tmp_path):
    manager, db, handle, adapter = make_manager(tmp_path)
    ask_ev = {
        "id": "evt_ask", "type": "agent.custom_tool_use", "processed_at": None,
        "name": "ask_user",
        "input": {"question": "Did you own incidents?", "context": "JD gap", "kind": "open"},
    }
    await feed_event(manager, adapter, handle, ask_ev)
    q = db.get_question(RUN_ID, "evt_ask")
    assert q is not None and q["question"] == "Did you own incidents?" and q["answer"] is None

    await feed_event(manager, adapter, handle, idle("evt_idle1", "evt_ask"))
    assert adapter.answers == []  # held for the human

    # restart recovery: answer recorded, a re-emitted idle re-delivers it
    db.record_answer(RUN_ID, "evt_ask", "yes, informally", False, "2026-01-01T00:00:00+00:00")
    await feed_event(manager, adapter, handle, idle("evt_idle2", "evt_ask"))
    assert adapter.answers == [("evt_ask", "yes, informally")]

    # the same idle id is never dispatched twice in-process
    await feed_event(manager, adapter, handle, idle("evt_idle2", "evt_ask"))
    assert len(adapter.answers) == 1


async def test_submit_draft_judges_and_batches(tmp_path):
    manager, db, handle, adapter = make_manager(tmp_path)
    draft_ev = {
        "id": "evt_draft1", "type": "agent.custom_tool_use", "processed_at": None,
        "name": "submit_draft",
        "input": {"draft": "Built the billing pipeline. Cut latency 40%.", "label": "impact-forward"},
    }
    plan_ev = {
        "id": "evt_plan", "type": "agent.custom_tool_use", "processed_at": None,
        "name": "update_plan", "input": {"steps": []},
    }
    await feed_event(manager, adapter, handle, draft_ev)
    await feed_event(manager, adapter, handle, plan_ev)
    # CMA batching: ONE idle blocking on BOTH ids — every id must resolve
    await feed_event(manager, adapter, handle, idle("evt_idle1", "evt_draft1", "evt_plan"))

    assert ("evt_plan", "ok") in adapter.answers
    draft_answers = [a for a in adapter.answers if a[0] == "evt_draft1"]
    assert len(draft_answers) == 1
    payload = json.loads(draft_answers[0][1])
    assert payload["result"] == "needs_revision"  # stub: first submission fails
    assert payload["instruction"] == JUDGE_INSTRUCTION
    assert payload["findings"][0]["severity"] == "medium"
    assert payload["findings"][0]["failure_mode"] == "fabrication"
    assert payload["findings"][0]["span"] == "Built the billing pipeline. Cut latency 40%."

    verdict = db.get_verdict(RUN_ID, "evt_draft1")
    assert verdict is not None and verdict["iteration"] == 1 and verdict["judge_model"] == "stub"
    assert verdict["judge_input"]["generated_resume"] == draft_ev["input"]["draft"]
    assert verdict["judge_input"]["research_findings"] == NONE_PROVIDED

    # the gateway.judge_verdict event went on the wire
    gw = db.get_event_by_id(RUN_ID, "gwevt_judge_evt_draft1")
    assert gw is not None and gw["result"] == "needs_revision" and gw["iteration"] == 1

    # second submission -> satisfied (stub)
    draft2 = {
        "id": "evt_draft2", "type": "agent.custom_tool_use", "processed_at": None,
        "name": "submit_draft", "input": {"draft": "Revised draft.", "label": "v2"},
    }
    await feed_event(manager, adapter, handle, draft2)
    await feed_event(manager, adapter, handle, idle("evt_idle2", "evt_draft2"))
    payload2 = json.loads([a for a in adapter.answers if a[0] == "evt_draft2"][0][1])
    assert payload2["result"] == "satisfied"
    assert db.get_verdict(RUN_ID, "evt_draft2")["iteration"] == 2

    # restart recovery: a re-dispatched judged draft re-sends the stored verdict, no re-judge
    await feed_event(manager, adapter, handle, idle("evt_idle3", "evt_draft1"))
    payload3 = json.loads([a for a in adapter.answers if a[0] == "evt_draft1"][-1][1])
    assert payload3["result"] == "needs_revision"
    assert db.count_verdicts(RUN_ID) == 2


async def test_submit_draft_inline_contract_fields(tmp_path):
    manager, db, handle, adapter = make_manager(tmp_path)
    draft_ev = {
        "id": "evt_d", "type": "agent.custom_tool_use", "processed_at": None,
        "name": "submit_draft",
        "input": {
            "draft": "Some draft.",
            "research_notes": "Acme ships weekly.",
            "gap_analysis": "No incident evidence.",
        },
    }
    await feed_event(manager, adapter, handle, draft_ev)
    await feed_event(manager, adapter, handle, idle("evt_i", "evt_d"))
    ji = db.get_verdict(RUN_ID, "evt_d")["judge_input"]
    assert ji["research_findings"] == "Acme ships weekly."
    assert ji["gap_analysis"] == "No incident evidence."


def test_render_judge_input_trusted_sources():
    qa = [
        {"question": "Q1?", "answer": "A1."},
        {"question": "Q2?", "answer": None},  # unanswered rounds are excluded
    ]
    ji = render_judge_input("RESUME", qa, "JOB", None, "", "DRAFT")
    assert ji.source_profile == "RESUME\n\nQ: Q1?\nA: A1."
    assert ji.job_posting == "JOB"
    assert ji.research_findings == NONE_PROVIDED
    assert ji.gap_analysis == NONE_PROVIDED
    assert ji.generated_resume == "DRAFT"

    ji = render_judge_input("RESUME", [], "", None, None, "DRAFT")
    assert ji.job_posting == NONE_PROVIDED


async def test_stub_determinism(tmp_path):
    settings = make_settings(tmp_path)
    ji = JudgeInput(
        source_profile="p", job_posting="j", research_findings="r", gap_analysis="g",
        generated_resume="# Head\n\nFirst real sentence here.\n\n- bullet",
    )
    v1 = await judge_draft(settings, ji, 1)
    v1_again = await judge_draft(settings, ji, 1)
    assert (v1.result, v1.findings) == (v1_again.result, v1_again.findings)
    assert v1.result == "needs_revision"
    assert v1.findings[0]["span"] == "First real sentence here."
    assert v1.rubric is None
    assert v1.prompt_version  # ACTIVE_VERSION recorded even in stub mode
    v2 = await judge_draft(settings, ji, 2)
    assert v2.result == "satisfied" and v2.findings == []


def test_stub_finding_skips_headings():
    findings = stub_findings("# Title\n\n- First bullet claim\n")
    assert findings[0]["span"] == "First bullet claim"
