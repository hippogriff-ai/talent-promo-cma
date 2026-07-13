"""Pydantic schemas for the resume grounding judge.

The Finding shape mirrors the human annotation schema used in the LangSmith
annotation queue (Phase B open coding): one finding per suspicious claim,
with a failure_mode + severity pair and the offending span quoted verbatim.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

FailureMode = Literal[
    "fabrication",
    "scope_conflation",
    "scale_attribution",
    "unit_inflation",
    "paraphrase_miss",
    "timeline_invention",
    "keyword_dilution",
    "other",
]

Severity = Literal["low", "medium", "high"]


class Finding(BaseModel):
    """One grounding failure found in a generated resume.

    In V2, product-level "ask the user" verdicts derive from findings
    (e.g. medium/high findings on claims the candidate could legitimately
    confirm route to the Q&A stage instead of being silently dropped).
    The judge itself stays aligned to what human coders annotate.
    """

    span: str = Field(description="Verbatim offending text quoted from the resume")
    failure_mode: FailureMode
    severity: Severity
    rationale: str = Field(
        description="Why this span fails its grounding contract, citing which input does or does not ground it"
    )


class GroundingVerdict(BaseModel):
    """Structured output of the grounding judge call. Empty findings = clean resume."""

    findings: list[Finding]


class RubricDimension(BaseModel):
    """Rationale-before-score for one quality dimension."""

    rationale: str
    score: int = Field(ge=1, le=5)


class RubricScores(BaseModel):
    """Structured output of the quality-rubric judge call."""

    jd_tailoring: RubricDimension
    ats_keywords: RubricDimension
    structure: RubricDimension
    impact_phrasing: RubricDimension


class JudgeInput(BaseModel):
    """The grounding contract plus the artifact under judgment.

    Field names mirror the annotation-queue trace inputs/outputs.
    generated_resume may be HTML; the runner normalizes it to text.
    """

    source_profile: str
    job_posting: str
    research_findings: str
    gap_analysis: str
    generated_resume: str


class JudgeOutput(BaseModel):
    findings: list[Finding]
    rubric: Optional[RubricScores] = None


class JudgePrompts(BaseModel):
    """One versioned prompt artifact set == one GEPA candidate.

    Component names match the GEPA candidate keys exactly.
    """

    version: str
    grounding_judge_prompt: str
    severity_calibration: str
    rubric_judge_prompt: str
