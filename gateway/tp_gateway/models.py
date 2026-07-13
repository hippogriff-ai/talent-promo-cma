"""Wire shapes from CONTRACT.md §2.

Snapshot pieces are TypedDicts (not pydantic) because the golden fold test
pins byte-identical JSON against the TS fold: fields typed `x?:` in the
contract must be OMITTED when absent, while fields typed `X|null` must be
emitted as null — dicts give exact key control; pydantic's exclude_none
can't distinguish the two. Request bodies are pydantic for validation.
"""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel

Engine = Literal["mock", "cma"]
RunStatus = Literal["working", "needs_you", "done", "failed"]


# ── request bodies ──────────────────────────────────────────────────────────


class CreateRunRequest(BaseModel):
    engine: Engine | None = None  # None → TP_DEFAULT_ENGINE
    title: str | None = None
    resume_text: str
    job_text: str | None = None
    job_url: str | None = None


class MessageRequest(BaseModel):
    text: str


class AnswerRequest(BaseModel):
    question_key: str
    text: str | None = None
    skip: bool = False


# ── snapshot / summary shapes (contract §2) ─────────────────────────────────


class PlanStep(TypedDict, total=False):
    id: str
    title: str
    status: str  # pending|active|done|skipped
    note: str  # optional — omit when absent


class Plan(TypedDict):
    steps: list[PlanStep]
    current_step_id: str | None
    stale: bool


class Question(TypedDict, total=False):
    question_key: str
    question: str
    context: str  # optional
    kind: str  # optional — open|confirm|choice
    options: list[str]  # optional
    asked_seq: int


class Draft(TypedDict, total=False):
    draft_id: str
    label: str
    summary: str  # optional
    draft: str
    seq: int


class JudgeFinding(TypedDict):
    span: str
    failure_mode: str
    severity: str  # low|medium|high
    rationale: str


class Verdict(TypedDict):
    draft_id: str
    result: str  # satisfied|needs_revision
    explanation: str
    iteration: int
    findings: list[JudgeFinding]
    rubric: dict[str, dict[str, Any]] | None


class FeedItem(TypedDict, total=False):
    seq: int
    kind: str  # user|agent|tool|system|verdict|error
    headline: str
    body: str  # optional
    collapsed: bool


class Usage(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usd: float | None


class Snapshot(TypedDict):
    run_id: str
    engine: str
    title: str
    status: RunStatus
    cursor: int
    plan: Plan | None
    feed: list[FeedItem]
    pending_questions: list[Question]
    drafts: list[Draft]
    verdicts: list[Verdict]
    usage: Usage


class RunSummary(TypedDict):
    run_id: str
    engine: str
    title: str
    status: RunStatus
    created_at: str
    needs_you: bool
    spend_usd: float | None
