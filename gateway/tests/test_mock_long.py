"""mock-long scenario tests (CONTRACT §8 mock-long).

Covers: realistic-scale stats sanity, the full HTTP drive (all four questions,
incl. the choice + confirm), plan revision history (skipped step with a note,
step added mid-run), the v1→v2 normalized-diff precondition (bullet churn folds
away, exactly 6 wording edits remain), stub-verdict findings counts, and the
regen-twice byte-identity of the golden fixture pair.
"""

import json
import re

from tp_gateway.engines.mock_long import (
    ADDED_STEP_NOTE,
    CANNED_ANSWERS,
    EXPECTED_INPUT_TOKENS,
    SKIPPED_STEP_NOTE,
)
from tp_gateway.fold import fold

from .conftest import (
    FIXTURE_CASES,
    LONG_FIXTURE_INPUTS,
    drive_mock_long_run,
    ensure_fixture_pair,
    generate_long_fixtures,
    poll_snapshot,
)


async def _long_events_and_snapshot(tmp_path):
    jsonl, _ = await ensure_fixture_pair("mock_run_long", tmp_path)
    events = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    case = FIXTURE_CASES["mock_run_long"]
    snap = fold(case["run_id"], case["engine"], case["title"], events, inputs=case["inputs"])
    return events, snap


async def test_long_scenario_stats(tmp_path):
    """§8 sizing: ≥90 events, 90+ feed items, 3 drafts, 4 questions, 15+ tool
    events, transient error + compaction, ~1.5M cumulative input tokens."""
    events, snap = await _long_events_and_snapshot(tmp_path)

    assert len(events) >= 90
    assert len(snap["feed"]) >= 90

    asks = [e for e in events if e.get("name") == "ask_user"]
    assert len(asks) == 4
    kinds = [a["input"].get("kind") for a in asks]
    assert kinds == ["choice", "confirm", "open", "open"]
    assert len(asks[0]["input"]["options"]) == 4
    assert all(len(a["input"]["context"]) > 200 for a in asks), "each question carries long context"

    plain_tools = [e for e in events if e["type"] == "agent.tool_use"]
    assert len(plain_tools) >= 15
    mem_writes = [e for e in plain_tools if e["name"] == "memory_write"]
    assert len(mem_writes) >= 5
    assert all(len(e["input"]["content"]) > 150 for e in mem_writes), "memory writes carry full files"
    assert sum(1 for e in mem_writes if len(e["input"]["content"]) > 800) >= 5, "long full-file contents"
    assert any(e["name"] == "web_search" for e in plain_tools)

    assert len(snap["drafts"]) == 3
    assert all(d["draft"].count("\n") >= 55 for d in snap["drafts"]), "long full-resume drafts"
    assert len(snap["pending_questions"]) == 0
    assert snap["status"] == "done"

    assert sum(1 for e in events if e["type"] == "session.error") == 1
    assert sum(1 for e in events if e["type"] == "agent.thread_context_compacted") == 1
    assert any(f["kind"] == "error" for f in snap["feed"])

    assert snap["usage"]["input_tokens"] == EXPECTED_INPUT_TOKENS
    assert 1_400_000 <= snap["usage"]["input_tokens"] <= 1_700_000


async def test_long_full_http_drive(app_and_client):
    """Full HTTP drive with TP_MOCK_DELAY_MS=0: create → 4 sequential questions
    (choice, confirm, open, open) → 3 judged drafts → done. Engine passes through."""
    app, client = app_and_client
    run_id = await drive_mock_long_run(client)

    snap = (await client.get(f"/api/coach/runs/{run_id}")).json()
    assert snap["engine"] == "mock-long"
    assert snap["status"] == "done"
    assert snap["inputs"] == LONG_FIXTURE_INPUTS
    assert len(snap["drafts"]) == 3
    assert [v["result"] for v in snap["verdicts"]] == ["needs_revision", "needs_revision", "satisfied"]
    # the choice answer and the incident answer are embedded in the final draft
    assert CANNED_ANSWERS[0] in snap["drafts"][2]["draft"]
    assert CANNED_ANSWERS[3] in snap["drafts"][2]["draft"]

    # RunSummary.engine passes through (§2)
    listed = (await client.get("/api/coach/runs")).json()["runs"]
    assert listed[0]["run_id"] == run_id
    assert listed[0]["engine"] == "mock-long"

    # export: all four Q&A recorded with the canned answers, none skipped
    bundle = (await client.get(f"/api/coach/runs/{run_id}/export")).json()
    assert bundle["run"]["agent_ref"] == {"engine": "mock-long"}
    assert [q["answer"] for q in bundle["qa"]] == CANNED_ANSWERS
    assert all(q["skipped"] is False for q in bundle["qa"])
    assert len(bundle["verdicts"]) == 3
    # judge stub only (mock must never spend money): stub model recorded
    assert all(v["judge_model"] == "stub" for v in bundle["verdicts"])
    # answers land verbatim in the judge's source_profile (§6.1 trust boundary)
    assert CANNED_ANSWERS[2] in bundle["verdicts"][2]["judge_input"]["source_profile"]


async def test_long_questions_block_until_answered(app_and_client):
    """The 4 questions are SEQUENTIAL and blocking: exactly one pending at a
    time, run sits in needs_you until each answer arrives."""
    app, client = app_and_client
    resp = await client.post(
        "/api/coach/runs",
        json={
            "engine": "mock-long",
            "resume_text": LONG_FIXTURE_INPUTS["resume_text"],
            "job_text": LONG_FIXTURE_INPUTS["job_text"],
        },
    )
    run_id = resp.json()["run_id"]
    seen_keys: list[str] = []
    for i, answer in enumerate(CANNED_ANSWERS):
        snap = await poll_snapshot(
            client,
            run_id,
            lambda s: any(q["question_key"] not in seen_keys for q in s["pending_questions"]),
            timeout_s=30.0,
        )
        assert snap["status"] == "needs_you"
        assert len(snap["pending_questions"]) == 1, "mock asks one blocking question at a time"
        q = snap["pending_questions"][0]
        seen_keys.append(q["question_key"])
        if i == 0:
            assert q["kind"] == "choice"
            assert len(q["options"]) == 4
            assert answer in q["options"], "the canned choice answer is one of the options"
        if i == 1:
            assert q["kind"] == "confirm"
        resp = await client.post(
            f"/api/coach/runs/{run_id}/answers", json={"question_key": q["question_key"], "text": answer}
        )
        assert resp.status_code == 202
    assert len(set(seen_keys)) == 4
    await poll_snapshot(client, run_id, lambda s: s["status"] == "done", timeout_s=30.0)


async def test_long_plan_revisions(tmp_path):
    """Plan: 9 steps in the end, revised 6+ times; one step SKIPPED with a note
    mid-run; one step ADDED mid-run."""
    events, snap = await _long_events_and_snapshot(tmp_path)

    plans = [e["input"] for e in events if e.get("name") == "update_plan"]
    assert len(plans) >= 7, "initial plan + at least 6 revisions"

    first, final = plans[0], plans[-1]
    assert len(first["steps"]) == 8
    assert len(final["steps"]) == 9
    assert snap["plan"] is not None
    assert len(snap["plan"]["steps"]) == 9

    # skipped step with note, present from mid-run onward
    skipped = [s for s in snap["plan"]["steps"] if s["status"] == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["id"] == "portfolio"
    assert skipped[0]["note"] == SKIPPED_STEP_NOTE
    first_skip_rev = next(i for i, p in enumerate(plans) if any(s["status"] == "skipped" for s in p["steps"]))
    assert 0 < first_skip_rev < len(plans) - 1, "the skip happens MID-run, not in the first/last plan"

    # added step: absent from the initial plan, present (with note) later
    assert "quantify" not in {s["id"] for s in first["steps"]}
    added = next(s for s in snap["plan"]["steps"] if s["id"] == "quantify")
    assert added["note"] == ADDED_STEP_NOTE
    assert added["status"] == "done"
    first_add_rev = next(i for i, p in enumerate(plans) if any(s["id"] == "quantify" for s in p["steps"]))
    assert 0 < first_add_rev < len(plans) - 1, "the add happens MID-run"

    # everything else lands done
    assert all(s["status"] == "done" for s in snap["plan"]["steps"] if s["id"] != "portfolio")


def _normalize_line(line: str) -> str:
    """Mirror of the web diff normalizer (talent-promo-web components/diff.ts
    normalizeLine): strip one leading bullet/number marker, collapse whitespace."""
    line = re.sub(r"^\s*(?:[-*•·‣▪–—]+|\(?\d{1,3}[.)]\)?)\s+", "", line)
    return re.sub(r"\s+", " ", line).strip()


async def test_long_normalized_diff_precondition(tmp_path):
    """§8: v2 = v1 + bullet-glyph/whitespace churn + exactly 6 wording edits.
    Under the web's line normalization the diff must show ONLY the 6."""
    _, snap = await _long_events_and_snapshot(tmp_path)
    v1 = snap["drafts"][0]["draft"].split("\n")
    v2 = snap["drafts"][1]["draft"].split("\n")

    assert len(v1) == len(v2), "churn+edits preserve the line count (edits are within-line)"
    raw_changed = [i for i, (a, b) in enumerate(zip(v1, v2)) if a != b]
    norm_changed = [i for i, (a, b) in enumerate(zip(v1, v2)) if _normalize_line(a) != _normalize_line(b)]
    assert len(norm_changed) == 6, f"normalized diff must be exactly the 6 edits, got {len(norm_changed)}"
    churn_only = set(raw_changed) - set(norm_changed)
    assert len(churn_only) >= 20, "the glyph/whitespace churn must be substantial"

    # v3 differs from v2 by real edits only (the final polish pass)
    v3 = snap["drafts"][2]["draft"].split("\n")
    assert v2 != v3


async def test_long_verdict_findings(tmp_path):
    """Stub verdicts: v1 needs_revision with 3 findings (verbatim draft spans),
    v2 needs_revision with 1, v3 satisfied with the full rubric."""
    _, snap = await _long_events_and_snapshot(tmp_path)

    verdicts = snap["verdicts"]
    assert [v["result"] for v in verdicts] == ["needs_revision", "needs_revision", "satisfied"]
    assert [v["iteration"] for v in verdicts] == [1, 2, 3]
    assert [len(v["findings"]) for v in verdicts] == [3, 1, 0]
    assert [v["draft_id"] for v in verdicts] == [d["draft_id"] for d in snap["drafts"]]

    # findings quote VERBATIM spans of the draft they judged
    for verdict, draft in zip(verdicts, snap["drafts"]):
        for f in verdict["findings"]:
            assert f["span"] in draft["draft"], f"span not verbatim in draft: {f['span']!r}"
            assert f["severity"] in ("low", "medium", "high")
            assert f["failure_mode"]
            assert f["rationale"]
    assert {f["failure_mode"] for f in verdicts[0]["findings"]} == {
        "scale_attribution",
        "unit_inflation",
        "fabrication",
    }

    # the flagged v1 spans are gone from v2; the v2 span is gone from v3
    for f in verdicts[0]["findings"]:
        assert f["span"] not in snap["drafts"][1]["draft"]
    assert verdicts[1]["findings"][0]["span"] not in snap["drafts"][2]["draft"]

    # full rubric on the satisfied verdict only
    assert verdicts[0]["rubric"] is None and verdicts[1]["rubric"] is None
    rubric = verdicts[2]["rubric"]
    assert set(rubric) == {"jd_tailoring", "ats_keywords", "structure", "impact_phrasing"}
    for dim in rubric.values():
        assert isinstance(dim["score"], int)
        assert dim["rationale"]


async def test_long_fixture_regen_is_byte_identical(tmp_path):
    """The deterministic conftest pattern: regenerating the long fixture pair
    twice yields byte-identical files (canned answers + pinned processed_at)."""
    dir_a = tmp_path / "gen_a"
    dir_b = tmp_path / "gen_b"
    await generate_long_fixtures(dir_a, tmp_path / "tmp_a")
    await generate_long_fixtures(dir_b, tmp_path / "tmp_b")
    for name in ("mock_run_long.jsonl", "mock_run_long.snapshot.json"):
        a = (dir_a / name).read_bytes()
        b = (dir_b / name).read_bytes()
        assert a == b, f"{name} not byte-identical across regenerations"
        assert len(a) > 10_000, f"{name} suspiciously small"
