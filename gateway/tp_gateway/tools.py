"""Custom-tool semantics (CONTRACT.md §4) + judge integration (§5).

Pure-ish helpers shared by the relay and scripts/smoke_cma.py: JudgeInput
rendering from TRUSTED sources only (resume + gateway-recorded Q&A — spec
§6.1), the deterministic judge stub, and the tool-result JSON shape.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from tp_gateway.config import Settings
from tp_gateway.judge import JudgeInput, load_judge_prompts, run_judge
from tp_gateway.judge.runner import DEFAULT_MODEL
from tp_gateway.judge.spans import normalize_resume_text

SKIP_ANSWER_TEXT = "[skipped — the candidate chose not to answer; move on]"
JUDGE_INSTRUCTION = "Address every finding — fix it, confirm it via ask_user, or cut it — and resubmit until clean."
NONE_PROVIDED = "(none provided)"
UPDATE_PLAN_ACK = "ok"


@dataclass
class JudgeVerdict:
    result: str  # satisfied | needs_revision
    explanation: str
    iteration: int
    findings: list[dict[str, Any]]
    rubric: dict[str, dict[str, Any]] | None
    judge_input: dict[str, str]
    judge_model: str
    prompt_version: str


def render_judge_input(
    resume_text: str,
    qa_rows: list[dict[str, Any]],
    job_text: str,
    research_findings: str | None,
    gap_analysis: str | None,
    draft_text: str,
) -> JudgeInput:
    """source_profile = resume + all gateway-recorded Q&A verbatim (§5)."""
    profile = resume_text.strip()
    blocks = [f"Q: {q['question']}\nA: {q['answer']}" for q in qa_rows if q.get("answer") is not None]
    if blocks:
        profile = profile + "\n\n" + "\n\n".join(blocks)
    return JudgeInput(
        source_profile=profile,
        job_posting=job_text.strip() or NONE_PROVIDED,
        research_findings=(research_findings or "").strip() or NONE_PROVIDED,
        gap_analysis=(gap_analysis or "").strip() or NONE_PROVIDED,
        generated_resume=draft_text,
    )


def _first_sentence(draft_text: str) -> str:
    """Deterministic 'any sentence of the draft' for the stub finding: the first
    non-heading, non-blank line, sans list marker."""
    for line in normalize_resume_text(draft_text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line.lstrip("-* ").strip()
    return draft_text.strip()[:120]


def stub_findings(draft_text: str) -> list[dict[str, Any]]:
    return [
        {
            "span": _first_sentence(draft_text),
            "failure_mode": "fabrication",
            "severity": "medium",
            "rationale": "[stub] Deterministic canned finding — no source in the grounding "
            "contract authorizes this span; confirm it via ask_user or cut it.",
        }
    ]


# ── mock-long stub (§8 mock-long): 3 findings → 1 finding → satisfied+rubric ──

_BULLET_GLYPHS = ("- ", "• ", "* ")


def _digit_bullets(draft_text: str) -> list[str]:
    """Deterministic claim extraction: bullet lines containing a digit, glyph
    stripped — the remainder is a VERBATIM span of the draft (contract §8:
    findings quote verbatim spans)."""
    out = []
    for line in draft_text.splitlines():
        line = line.strip()
        if line.startswith(_BULLET_GLYPHS) and any(c.isdigit() for c in line):
            out.append(line[2:].strip())
    return out


_LONG_ROUND1 = [
    (
        0,
        "scale_attribution",
        "high",
        "[stub] Neither the resume nor any recorded answer supports this scale claim — "
        "the sourced figure is 14 utility customers. Replace the claim with the number "
        "a reference check would confirm.",
    ),
    (
        2,
        "unit_inflation",
        "medium",
        "[stub] The multiplier and audience here inflate the sourced metrics — the "
        "grounding contract gives p75 page load 4.2s → 1.1s and tripled operator seats, "
        "not this framing. Use the measured numbers.",
    ),
    (
        4,
        "fabrication",
        "medium",
        "[stub] No source in the grounding contract mentions this durability/loss claim; "
        "the only availability figure on record is the candidate's 99.95% ingest answer. "
        "State that, with its qualifier, or cut the line.",
    ),
]

_LONG_ROUND2 = [
    (
        6,
        "paraphrase_miss",
        "medium",
        "[stub] The source says ticket-resolution time was cut 'roughly in half' but gives "
        "no baseline — the parenthetical baseline appears in no source. Drop it or confirm "
        "it via ask_user.",
    ),
]

LONG_STUB_RUBRIC: dict[str, dict[str, Any]] = {
    "jd_tailoring": {
        "score": 5,
        "rationale": "[stub] Anchored on the pillar the posting hires for; the JD's own "
        "priority order (operational ownership, Staff scope, stack depth) is mirrored by "
        "the section order.",
    },
    "ats_keywords": {
        "score": 4,
        "rationale": "[stub] Kafka, ClickHouse, TypeScript/React, Go, on-call all appear "
        "inside claim sentences rather than a keyword list; GraphQL federation absent but "
        "not required.",
    },
    "structure": {
        "score": 5,
        "rationale": "[stub] One anchor story with support bullets, a dedicated "
        "operational-ownership section, and dated artifacts — scannable in under a minute.",
    },
    "impact_phrasing": {
        "score": 4,
        "rationale": "[stub] Every metric carries its qualifier and source; two bullets "
        "remain qualitative where the candidate declined to invent numbers.",
    },
}


def long_stub_verdict(draft_text: str, iteration: int) -> tuple[list[dict[str, Any]], dict | None]:
    """Deterministic mock-long stub: round 1 → 3 findings, round 2 → 1, round 3+
    → satisfied with the full rubric. Spans are verbatim lines of the draft."""
    claims = _digit_bullets(draft_text)
    if not claims:  # degenerate drafts: fall back to the minimal stub behavior
        return (stub_findings(draft_text) if iteration == 1 else [], None if iteration < 3 else LONG_STUB_RUBRIC)
    plan = _LONG_ROUND1 if iteration == 1 else _LONG_ROUND2 if iteration == 2 else []
    findings = [
        {
            "span": claims[min(idx, len(claims) - 1)],
            "failure_mode": mode,
            "severity": severity,
            "rationale": rationale,
        }
        for idx, mode, severity, rationale in plan
    ]
    return findings, (LONG_STUB_RUBRIC if not findings else None)


def _explanation(findings: list[dict[str, Any]]) -> str:
    """USER-facing (§3): shown as the verdict feed body. The revise-and-resubmit
    imperative is agent-facing and lives ONLY in the tool result's `instruction`
    field (JUDGE_INSTRUCTION, §4) — never leak it here."""
    if not findings:
        return "No grounding failures found."
    return f"Grounding review found {len(findings)} finding(s)."


async def judge_draft(
    settings: Settings, judge_input: JudgeInput, iteration: int, engine: str
) -> JudgeVerdict:
    """Run the judge (stub per §5: always for mock runs unless TP_JUDGE_STUB=0,
    else when OPENAI_API_KEY is unset or TP_JUDGE_STUB=1).

    `iteration` is 1-based per run (prior verdict count + 1); the stub fails
    iteration 1 and passes the rest.
    """
    prompts = load_judge_prompts()
    if settings.judge_stub_for(engine):
        rubric: dict[str, dict[str, Any]] | None
        if engine == "mock-long":
            # §8 mock-long: 3 findings → 1 finding → satisfied with a full rubric
            findings, rubric = long_stub_verdict(judge_input.generated_resume, iteration)
        else:
            findings = stub_findings(judge_input.generated_resume) if iteration == 1 else []
            rubric = None
        model = "stub"
    else:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        output = await asyncio.to_thread(run_judge, judge_input, prompts, client, DEFAULT_MODEL)
        findings = [f.model_dump() for f in output.findings]
        rubric = (
            {dim: {"score": d["score"], "rationale": d["rationale"]} for dim, d in output.rubric.model_dump().items()}
            if output.rubric is not None
            else None
        )
        model = DEFAULT_MODEL
    result = "satisfied" if not findings else "needs_revision"
    return JudgeVerdict(
        result=result,
        explanation=_explanation(findings),
        iteration=iteration,
        findings=findings,
        rubric=rubric,
        judge_input=judge_input.model_dump(),
        judge_model=model,
        prompt_version=prompts.version,
    )


def verdict_tool_result(result: str, explanation: str, findings: list[dict[str, Any]], rubric: Any) -> str:
    """Compact JSON returned to the agent as the submit_draft tool result (§4)."""
    return json.dumps(
        {
            "result": result,
            "explanation": explanation,
            "findings": findings,
            "rubric": rubric,
            "instruction": JUDGE_INSTRUCTION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def verdict_wire_event(draft_id: str, verdict: JudgeVerdict, processed_at: str) -> dict[str, Any]:
    """The gateway-authored gateway.judge_verdict WireEvent (§3)."""
    return {
        "id": f"gwevt_judge_{draft_id}",
        "type": "gateway.judge_verdict",
        "processed_at": processed_at,
        "draft_id": draft_id,
        "result": verdict.result,
        "explanation": verdict.explanation,
        "iteration": verdict.iteration,
        "findings": verdict.findings,
        "rubric": verdict.rubric,
    }
