// SSE consumer for /api/coach/runs/{id}/events (CONTRACT §2/§3).
//
// - bootstraps from GET /api/coach/runs/{id} (the gateway snapshot), then tails
//   the SSE stream with ?cursor=snapshot.cursor
// - upsert-by-id event map: the same id may re-arrive with processed_at flipped
//   null→set — replace, don't duplicate (and never fold it twice)
// - auto-reconnect with exponential backoff, resuming from the local cursor

import { API_URL } from "./api";
import { applyEvent, stateFromSnapshot, toSnapshot, type FoldState } from "./fold";
import type { Snapshot, WireEvent } from "./types";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

export type RunStreamHandlers = {
  onSnapshot: (snap: Snapshot) => void;
  onConnection?: (state: ConnectionState) => void;
  onError?: (message: string) => void;
};

export type RunStream = { close(): void };

const MAX_BACKOFF_MS = 15_000;

export function connectRun(runId: string, handlers: RunStreamHandlers): RunStream {
  let closed = false;
  let es: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let state: FoldState | null = null;
  const seen = new Map<string, WireEvent>();

  const emit = () => {
    if (state && !closed) handlers.onSnapshot(toSnapshot(state));
  };
  const setConn = (c: ConnectionState) => {
    if (!closed || c === "closed") handlers.onConnection?.(c);
  };

  function scheduleRetry(fn: () => void) {
    if (closed) return;
    const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
    attempt++;
    timer = setTimeout(fn, delay);
  }

  async function bootstrap() {
    setConn("connecting");
    try {
      const res = await fetch(`${API_URL}/api/coach/runs/${runId}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`snapshot fetch failed (HTTP ${res.status})`);
      const snap = (await res.json()) as Snapshot;
      if (closed) return;
      state = stateFromSnapshot(snap);
      emit();
      openStream();
    } catch (e) {
      if (closed) return;
      handlers.onError?.(e instanceof Error ? e.message : String(e));
      setConn("reconnecting");
      scheduleRetry(bootstrap);
    }
  }

  function openStream() {
    if (closed || !state) return;
    es = new EventSource(`${API_URL}/api/coach/runs/${runId}/events?cursor=${state.cursor}`);

    es.onopen = () => {
      attempt = 0;
      setConn("live");
    };

    es.onmessage = (msg) => {
      let ev: WireEvent;
      try {
        ev = JSON.parse(msg.data) as WireEvent;
      } catch {
        return; // malformed frame: ignore
      }
      if (!ev || typeof ev.id !== "string" || !state) return;
      const prev = seen.get(ev.id);
      seen.set(ev.id, ev); // upsert by id
      if (prev) {
        // replacement (e.g. processed_at flip) — the fold ignores processed_at,
        // so just advance the cursor; never fold the same id twice
        if (typeof ev.seq === "number") state.cursor = Math.max(state.cursor, ev.seq);
      } else {
        applyEvent(state, ev);
      }
      emit();
    };

    es.onerror = () => {
      es?.close();
      es = null;
      if (closed) return;
      setConn("reconnecting");
      scheduleRetry(openStream); // resumes from state.cursor
    };
  }

  void bootstrap();

  return {
    close() {
      closed = true;
      if (timer) clearTimeout(timer);
      es?.close();
      es = null;
      handlers.onConnection?.("closed");
    },
  };
}
