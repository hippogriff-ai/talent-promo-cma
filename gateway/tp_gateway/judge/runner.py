"""Run the resume judge: grounding findings + optional quality rubric.

Two structured-output calls against small models. The message builders are
public so the eval harness can route the same messages through its caching
LLM wrapper; run_judge() is the direct product-facing entry point.

The client is injected (never constructed at import) so this module stays
importable without credentials and unit-testable offline.
"""

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from tp_gateway.judge.schemas import (
    GroundingVerdict,
    JudgeInput,
    JudgeOutput,
    JudgePrompts,
    RubricScores,
)
from tp_gateway.judge.spans import normalize_resume_text

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 4


def _grounding_system(prompts: JudgePrompts) -> str:
    return f"{prompts.grounding_judge_prompt}\n\n# SEVERITY CALIBRATION\n\n{prompts.severity_calibration}"


def _contract_sections(inputs: JudgeInput) -> str:
    return (
        f"## SOURCE PROFILE\n{inputs.source_profile}\n\n"
        f"## JOB POSTING\n{inputs.job_posting}\n\n"
        f"## RESEARCH FINDINGS\n{inputs.research_findings}\n\n"
        f"## GAP ANALYSIS\n{inputs.gap_analysis}"
    )


def build_grounding_messages(inputs: JudgeInput, prompts: JudgePrompts) -> list[ChatCompletionMessageParam]:
    resume_text = normalize_resume_text(inputs.generated_resume)
    user = (
        f"{_contract_sections(inputs)}\n\n"
        f"## GENERATED RESUME\n{resume_text}\n\n"
        "Code the GENERATED RESUME for grounding failures. Quote each offending span "
        "verbatim from the GENERATED RESUME text above."
    )
    return [
        {"role": "system", "content": _grounding_system(prompts)},
        {"role": "user", "content": user},
    ]


def build_rubric_messages(inputs: JudgeInput, prompts: JudgePrompts) -> list[ChatCompletionMessageParam]:
    resume_text = normalize_resume_text(inputs.generated_resume)
    user = (
        f"## JOB POSTING\n{inputs.job_posting}\n\n"
        f"## RESEARCH FINDINGS\n{inputs.research_findings}\n\n"
        f"## GENERATED RESUME\n{resume_text}\n\n"
        "Score the GENERATED RESUME on each rubric dimension."
    )
    return [
        {"role": "system", "content": prompts.rubric_judge_prompt},
        {"role": "user", "content": user},
    ]


def run_judge(
    inputs: JudgeInput,
    prompts: JudgePrompts,
    client: OpenAI,
    model: str = DEFAULT_MODEL,
    include_rubric: bool = True,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> JudgeOutput:
    configured = client.with_options(timeout=timeout_s, max_retries=max_retries)

    grounding_completion = configured.chat.completions.parse(
        model=model,
        messages=build_grounding_messages(inputs, prompts),
        response_format=GroundingVerdict,
    )
    verdict = grounding_completion.choices[0].message.parsed
    if verdict is None:
        raise ValueError("grounding judge returned no parsed output (refusal or length cutoff)")

    rubric: RubricScores | None = None
    if include_rubric:
        rubric_completion = configured.chat.completions.parse(
            model=model,
            messages=build_rubric_messages(inputs, prompts),
            response_format=RubricScores,
        )
        rubric = rubric_completion.choices[0].message.parsed
        if rubric is None:
            raise ValueError("rubric judge returned no parsed output (refusal or length cutoff)")

    return JudgeOutput(findings=verdict.findings, rubric=rubric)
