"""LLM-as-a-judge for generated resume drafts.

The judge checks a generated resume against its grounding contract
(source_profile / job_posting / research_findings / gap_analysis) and emits
span-level findings using the Phase B open-coding failure-mode taxonomy,
plus an optional quality rubric.

Prompts are versioned artifacts under judge/prompts/ (see prompt_loader).
The GEPA optimization harness that tunes them lives in the top-level evals/
package and is intentionally not imported here.
"""

from tp_gateway.judge.prompt_loader import load_judge_prompts
from tp_gateway.judge.runner import run_judge
from tp_gateway.judge.schemas import (
    Finding,
    GroundingVerdict,
    JudgeInput,
    JudgeOutput,
    JudgePrompts,
    RubricScores,
)

__all__ = [
    "Finding",
    "GroundingVerdict",
    "JudgeInput",
    "JudgeOutput",
    "JudgePrompts",
    "RubricScores",
    "load_judge_prompts",
    "run_judge",
]
