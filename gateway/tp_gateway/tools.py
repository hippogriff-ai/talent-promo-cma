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


def _explanation(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "No grounding failures found."
    return f"Grounding review found {len(findings)} finding(s); address each and resubmit."


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
        findings = stub_findings(judge_input.generated_resume) if iteration == 1 else []
        rubric: dict[str, dict[str, Any]] | None = None
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
