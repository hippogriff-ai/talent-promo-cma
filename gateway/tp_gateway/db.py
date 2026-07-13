"""SQLite persistence — stdlib sqlite3 behind a threading.Lock, no ORM.

Single-user local gateway: every statement is sub-millisecond, so sync calls
from async handlers are acceptable (documented constraint). The lock also
serializes per-run seq assignment, which is what makes `seq` monotonic.

Wire events are stored WITHOUT their seq in the payload column; readers merge
`{"seq": seq, **payload}` back. `event_id` is UNIQUE per run: re-arrival of the
same id (processed_at flip) updates the payload in place and keeps the seq.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  engine      TEXT NOT NULL,
  title       TEXT NOT NULL,
  resume_text TEXT NOT NULL,
  job_text    TEXT NOT NULL DEFAULT '',
  job_url     TEXT,
  session_id  TEXT,
  agent_ref   TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  run_id   TEXT NOT NULL,
  seq      INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  type     TEXT NOT NULL,
  payload  TEXT NOT NULL,
  PRIMARY KEY (run_id, seq),
  UNIQUE (run_id, event_id)
);
CREATE TABLE IF NOT EXISTS questions (
  run_id       TEXT NOT NULL,
  question_key TEXT NOT NULL,
  question     TEXT NOT NULL,
  context      TEXT,
  kind         TEXT,
  options      TEXT,
  asked_seq    INTEGER NOT NULL,
  asked_at     TEXT NOT NULL,
  answer       TEXT,
  skipped      INTEGER NOT NULL DEFAULT 0,
  answered_at  TEXT,
  PRIMARY KEY (run_id, question_key)
);
CREATE TABLE IF NOT EXISTS plans (
  run_id          TEXT NOT NULL,
  seq             INTEGER NOT NULL,
  steps           TEXT NOT NULL,
  current_step_id TEXT,
  PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS drafts (
  run_id   TEXT NOT NULL,
  draft_id TEXT NOT NULL,
  label    TEXT NOT NULL,
  summary  TEXT,
  draft    TEXT NOT NULL,
  seq      INTEGER NOT NULL,
  PRIMARY KEY (run_id, draft_id)
);
CREATE TABLE IF NOT EXISTS verdicts (
  run_id         TEXT NOT NULL,
  draft_id       TEXT NOT NULL,
  result         TEXT NOT NULL,
  explanation    TEXT NOT NULL,
  iteration      INTEGER NOT NULL,
  findings       TEXT NOT NULL,
  rubric         TEXT,
  judge_input    TEXT NOT NULL,
  judge_model    TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (run_id, draft_id)
);
"""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class Database:
    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── runs ────────────────────────────────────────────────────────────────

    def insert_run(
        self,
        run_id: str,
        engine: str,
        title: str,
        resume_text: str,
        job_text: str,
        job_url: str | None,
        agent_ref: dict[str, Any],
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, engine, title, resume_text, job_text, job_url, agent_ref, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (run_id, engine, title, resume_text, job_text, job_url, _dumps(agent_ref), created_at),
            )
            self._conn.commit()

    def set_run_session(self, run_id: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE runs SET session_id=? WHERE run_id=?", (session_id, run_id))
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._run_dict(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._run_dict(r) for r in rows]

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["agent_ref"] = json.loads(d["agent_ref"])
        return d

    # ── events ──────────────────────────────────────────────────────────────

    def upsert_event(self, run_id: str, event: dict[str, Any]) -> tuple[int, bool]:
        """Insert (new seq) or update-in-place (same seq) by event id.

        Returns (seq, inserted). `event` must carry id/type; any `seq` key in it
        is ignored (seq is gateway-assigned here).
        """
        payload = {k: v for k, v in event.items() if k != "seq"}
        with self._lock:
            row = self._conn.execute(
                "SELECT seq FROM events WHERE run_id=? AND event_id=?", (run_id, payload["id"])
            ).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE events SET type=?, payload=? WHERE run_id=? AND event_id=?",
                    (payload["type"], _dumps(payload), run_id, payload["id"]),
                )
                self._conn.commit()
                return int(row["seq"]), False
            nxt = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS s FROM events WHERE run_id=?", (run_id,)
            ).fetchone()
            seq = int(nxt["s"])
            self._conn.execute(
                "INSERT INTO events (run_id, seq, event_id, type, payload) VALUES (?,?,?,?,?)",
                (run_id, seq, payload["id"], payload["type"], _dumps(payload)),
            )
            self._conn.commit()
            return seq, True

    def get_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, payload FROM events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, after_seq),
            ).fetchall()
        return [{"seq": int(r["seq"]), **json.loads(r["payload"])} for r in rows]

    def get_event_by_id(self, run_id: str, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, payload FROM events WHERE run_id=? AND event_id=?", (run_id, event_id)
            ).fetchone()
        return {"seq": int(row["seq"]), **json.loads(row["payload"])} if row else None

    def last_event(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, payload FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)
            ).fetchone()
        return {"seq": int(row["seq"]), **json.loads(row["payload"])} if row else None

    # ── questions ───────────────────────────────────────────────────────────

    def upsert_question(
        self,
        run_id: str,
        question_key: str,
        question: str,
        context: str | None,
        kind: str | None,
        options: list[str] | None,
        asked_seq: int,
        asked_at: str,
    ) -> None:
        """Insert if new; re-arrival of the same key keeps any recorded answer."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO questions (run_id, question_key, question, context, kind, options, asked_seq, asked_at)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id, question_key) DO UPDATE SET"
                " question=excluded.question, context=excluded.context, kind=excluded.kind, options=excluded.options",
                (
                    run_id,
                    question_key,
                    question,
                    context,
                    kind,
                    _dumps(options) if options is not None else None,
                    asked_seq,
                    asked_at,
                ),
            )
            self._conn.commit()

    def get_question(self, run_id: str, question_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM questions WHERE run_id=? AND question_key=?", (run_id, question_key)
            ).fetchone()
        return self._question_dict(row) if row else None

    def list_questions(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM questions WHERE run_id=? ORDER BY asked_seq", (run_id,)
            ).fetchall()
        return [self._question_dict(r) for r in rows]

    @staticmethod
    def _question_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["options"] = json.loads(d["options"]) if d["options"] else None
        d["skipped"] = bool(d["skipped"])
        return d

    def record_answer(self, run_id: str, question_key: str, answer: str, skipped: bool, answered_at: str) -> bool:
        """Record an answer; False if the key is unknown or already answered."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE questions SET answer=?, skipped=?, answered_at=?"
                " WHERE run_id=? AND question_key=? AND answer IS NULL",
                (answer, int(skipped), answered_at, run_id, question_key),
            )
            self._conn.commit()
            return cur.rowcount == 1

    # ── plans / drafts / verdicts ───────────────────────────────────────────

    def upsert_plan(self, run_id: str, seq: int, steps: list[dict[str, Any]], current_step_id: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO plans (run_id, seq, steps, current_step_id) VALUES (?,?,?,?)",
                (run_id, seq, _dumps(steps), current_step_id),
            )
            self._conn.commit()

    def list_plans(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM plans WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [
            {"seq": int(r["seq"]), "steps": json.loads(r["steps"]), "current_step_id": r["current_step_id"]}
            for r in rows
        ]

    def upsert_draft(self, run_id: str, draft_id: str, label: str, summary: str | None, draft: str, seq: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO drafts (run_id, draft_id, label, summary, draft, seq) VALUES (?,?,?,?,?,?)",
                (run_id, draft_id, label, summary, draft, seq),
            )
            self._conn.commit()

    def get_draft(self, run_id: str, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM drafts WHERE run_id=? AND draft_id=?", (run_id, draft_id)
            ).fetchone()
        return dict(row) if row else None

    def list_drafts(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM drafts WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def insert_verdict(
        self,
        run_id: str,
        draft_id: str,
        result: str,
        explanation: str,
        iteration: int,
        findings: list[dict[str, Any]],
        rubric: dict[str, Any] | None,
        judge_input: dict[str, str],
        judge_model: str,
        prompt_version: str,
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO verdicts (run_id, draft_id, result, explanation, iteration, findings,"
                " rubric, judge_input, judge_model, prompt_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    draft_id,
                    result,
                    explanation,
                    iteration,
                    _dumps(findings),
                    _dumps(rubric) if rubric is not None else None,
                    _dumps(judge_input),
                    judge_model,
                    prompt_version,
                    created_at,
                ),
            )
            self._conn.commit()

    def get_verdict(self, run_id: str, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM verdicts WHERE run_id=? AND draft_id=?", (run_id, draft_id)
            ).fetchone()
        return self._verdict_dict(row) if row else None

    def list_verdicts(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM verdicts WHERE run_id=? ORDER BY iteration", (run_id,)
            ).fetchall()
        return [self._verdict_dict(r) for r in rows]

    def count_verdicts(self, run_id: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM verdicts WHERE run_id=?", (run_id,)).fetchone()
        return int(row["n"])

    @staticmethod
    def _verdict_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["findings"] = json.loads(d["findings"])
        d["rubric"] = json.loads(d["rubric"]) if d["rubric"] else None
        d["judge_input"] = json.loads(d["judge_input"])
        return d
