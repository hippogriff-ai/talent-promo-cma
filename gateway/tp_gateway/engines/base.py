"""EngineAdapter protocol (spec §2.2). WireEvent is a plain dict in MA shape:
{id, type, processed_at, ...payload} — seq is assigned by the gateway, never
by an engine."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict

WireEvent = dict[str, Any]


@dataclass
class RunSpec:
    run_id: str
    title: str
    resume_text: str
    job_text: str
    job_url: str | None = None
    engine: str = "mock"  # engine string from the run row ("mock" | "mock-long" | "cma")


@dataclass
class RunHandle:
    run_id: str
    engine: str
    session_id: str
    extra: dict[str, Any] = field(default_factory=dict)


class UsageNorm(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usd: float | None


class EngineAdapter(Protocol):
    async def create_run(self, spec: RunSpec) -> RunHandle: ...

    def events(self, h: RunHandle, cursor: str | None = None) -> AsyncIterator[WireEvent]:
        """Consolidated live event iterator. The gateway's SQLite is the replay
        surface; engines only need to deliver every event at least once (the
        gateway upserts by id)."""
        ...

    async def send_message(self, h: RunHandle, text: str) -> None: ...

    async def answer(self, h: RunHandle, question_key: str, content: str) -> None: ...

    async def interrupt(self, h: RunHandle) -> None: ...

    async def usage(self, h: RunHandle) -> UsageNorm: ...
