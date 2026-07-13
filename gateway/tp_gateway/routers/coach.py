"""Browser-facing coach API (CONTRACT.md §2) + evidence export (§7)."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tp_gateway.db import Database
from tp_gateway.fold import fold
from tp_gateway.judge.spans import html_to_text, looks_like_html
from tp_gateway.models import AnswerRequest, CreateRunRequest, MessageRequest, RunSummary, Snapshot
from tp_gateway.relay import RunManager
from tp_gateway.tools import SKIP_ANSWER_TEXT

router = APIRouter(prefix="/api/coach")

_HEARTBEAT_S = 15.0


def _manager(request: Request) -> RunManager:
    return request.app.state.manager


def _db(request: Request) -> Database:
    return request.app.state.db


def _snapshot(db: Database, run: dict[str, Any]) -> Snapshot:
    return fold(run["run_id"], run["engine"], run["title"], db.get_events(run["run_id"]))


def _summary(db: Database, run: dict[str, Any]) -> RunSummary:
    snap = _snapshot(db, run)
    return {
        "run_id": run["run_id"],
        "engine": run["engine"],
        "title": run["title"],
        "status": snap["status"],
        "created_at": run["created_at"],
        "needs_you": snap["status"] == "needs_you",
        "spend_usd": snap["usage"]["usd"],
    }


def _get_run_or_404(db: Database, run_id: str) -> dict[str, Any]:
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    return run


async def _fetch_job_text(url: str) -> str:
    """Best-effort fetch of a job posting URL to text (judge grounding input)."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.text
        return html_to_text(body) if looks_like_html(body) else body
    except Exception:
        return ""  # the agent can still web_fetch the URL itself


@router.post("/runs", status_code=201)
async def create_run(request: Request, body: CreateRunRequest) -> dict[str, str]:
    manager = _manager(request)
    engine = body.engine or manager.settings.tp_default_engine
    if engine not in ("mock", "cma"):
        raise HTTPException(status_code=400, detail=f"unknown engine: {engine}")
    if not body.resume_text.strip():
        raise HTTPException(status_code=400, detail="resume_text is required")
    if not (body.job_text or body.job_url):
        raise HTTPException(status_code=400, detail="at least one of job_text/job_url is required")
    if engine == "cma" and not manager.settings.cma_configured:
        raise HTTPException(status_code=400, detail="cma engine not configured (ANTHROPIC_API_KEY / CMA_* missing)")
    job_text = body.job_text or ""
    if not job_text and body.job_url:
        job_text = await _fetch_job_text(body.job_url)
    run_id = await manager.create_run(
        engine=engine,
        title=body.title or "Untitled run",
        resume_text=body.resume_text,
        job_text=job_text,
        job_url=body.job_url,
    )
    return {"run_id": run_id}


@router.get("/runs", response_model=None)
async def list_runs(request: Request) -> JSONResponse:
    db = _db(request)
    return JSONResponse({"runs": [_summary(db, run) for run in db.list_runs()]})


@router.get("/runs/{run_id}", response_model=None)
async def get_snapshot(request: Request, run_id: str) -> JSONResponse:
    # plain JSONResponse: the Snapshot's optional-key-omission semantics must
    # reach the wire exactly as the fold built them (golden fold contract)
    db = _db(request)
    run = _get_run_or_404(db, run_id)
    _manager(request).ensure_relay(run_id)
    return JSONResponse(_snapshot(db, run))


async def sse_frames(manager: RunManager, db: Database, run_id: str, cursor: int, heartbeat_s: float = _HEARTBEAT_S):
    """SSE frame generator: SQLite replay (seq > cursor), then live tail with
    `: heartbeat` comments every 15s. Module-level so tests can consume it
    directly (httpx's ASGITransport buffers full responses — an endless SSE
    body can't be driven through it)."""
    # subscribe BEFORE replaying so nothing falls in the gap; the client
    # upserts by id, so an occasional duplicate frame is protocol-safe.
    q = manager.subscribe(run_id)
    try:
        for ev in db.get_events(run_id, after_seq=cursor):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=heartbeat_s)
            except (asyncio.TimeoutError, TimeoutError):
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
    finally:
        manager.unsubscribe(run_id, q)


@router.get("/runs/{run_id}/events")
async def stream_events(request: Request, run_id: str, cursor: int = 0) -> StreamingResponse:
    db = _db(request)
    _get_run_or_404(db, run_id)
    manager = _manager(request)
    manager.ensure_relay(run_id)
    return StreamingResponse(
        sse_frames(manager, db, run_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/messages", status_code=202)
async def send_message(request: Request, run_id: str, body: MessageRequest) -> dict:
    db = _db(request)
    run = _get_run_or_404(db, run_id)
    manager = _manager(request)
    manager.ensure_relay(run_id)
    handle = manager.get_handle(run_id)
    if handle is None:
        raise HTTPException(status_code=409, detail="run is not live (mock runs do not survive restarts)")
    await manager.adapter(run["engine"]).send_message(handle, body.text)
    return {}


@router.post("/runs/{run_id}/answers", status_code=202)
async def answer_question(request: Request, run_id: str, body: AnswerRequest) -> dict:
    db = _db(request)
    run = _get_run_or_404(db, run_id)
    if body.skip:
        text = SKIP_ANSWER_TEXT
    else:
        if body.text is None or not body.text.strip():
            raise HTTPException(status_code=400, detail="text is required unless skip=true")
        text = body.text
    question = db.get_question(run_id, body.question_key)
    if question is None:
        raise HTTPException(status_code=409, detail=f"unknown question_key: {body.question_key}")
    recorded = db.record_answer(run_id, body.question_key, text, body.skip, _now_iso())
    if not recorded:
        raise HTTPException(status_code=409, detail="question already answered")
    manager = _manager(request)
    manager.ensure_relay(run_id)
    handle = manager.get_handle(run_id)
    if handle is not None:
        await manager.adapter(run["engine"]).answer(handle, body.question_key, text)
    return {}


@router.post("/runs/{run_id}/interrupt", status_code=202)
async def interrupt_run(request: Request, run_id: str) -> dict:
    db = _db(request)
    run = _get_run_or_404(db, run_id)
    manager = _manager(request)
    handle = manager.get_handle(run_id)
    if handle is not None:
        await manager.adapter(run["engine"]).interrupt(handle)
    return {}


@router.get("/runs/{run_id}/export")
async def export_run(request: Request, run_id: str) -> JSONResponse:
    db = _db(request)
    run = _get_run_or_404(db, run_id)
    snap = _snapshot(db, run)
    qa = [
        {
            "question_key": q["question_key"],
            "question": q["question"],
            **({"context": q["context"]} if q["context"] else {}),
            "answer": q["answer"],
            "skipped": q["skipped"],
            "asked_at": q["asked_at"],
            "answered_at": q["answered_at"],
        }
        for q in db.list_questions(run_id)
    ]
    drafts = [
        {
            "draft_id": d["draft_id"],
            "label": d["label"],
            **({"summary": d["summary"]} if d["summary"] else {}),
            "draft": d["draft"],
            "seq": d["seq"],
        }
        for d in db.list_drafts(run_id)
    ]
    verdicts = [
        {
            "draft_id": v["draft_id"],
            "result": v["result"],
            "explanation": v["explanation"],
            "iteration": v["iteration"],
            "findings": v["findings"],
            "rubric": v["rubric"],
            "judge_input": v["judge_input"],
            "judge_model": v["judge_model"],
            "prompt_version": v["prompt_version"],
        }
        for v in db.list_verdicts(run_id)
    ]
    bundle = {
        "run": {
            **_summary(db, run),
            "resume_text": run["resume_text"],
            "job_text": run["job_text"],
            "job_url": run["job_url"],
            "agent_ref": run["agent_ref"],
        },
        "events": db.get_events(run_id),
        "qa": qa,
        "drafts": drafts,
        "verdicts": verdicts,
        "plan_history": db.list_plans(run_id),
        "usage": snap["usage"],
        "exported_at": _now_iso(),
    }
    return JSONResponse(bundle)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
