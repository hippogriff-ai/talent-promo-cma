// Golden fold test (CONTRACT §9): the TS fold over gateway/tests/fixtures/mock_run.jsonl
// must deep-equal gateway/tests/fixtures/mock_run.snapshot.json (the Python fold's output).
//
// If the fixtures are absent (the gateway builder generates them), this test FAILS
// loudly — it must never pass vacuously.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { foldEvents } from "../lib/fold";
import type { Snapshot, WireEvent } from "../lib/types";

const FIXTURES_DIR = path.resolve(__dirname, "..", "..", "gateway", "tests", "fixtures");
const JSONL_PATH = path.join(FIXTURES_DIR, "mock_run.jsonl");
const SNAPSHOT_PATH = path.join(FIXTURES_DIR, "mock_run.snapshot.json");

/** Recursively sort object keys; JSON round-trip also drops `undefined` members. */
function canonicalize(value: unknown): unknown {
  return JSON.parse(stableStringify(value));
}

function stableStringify(value: unknown): string {
  const sortKeys = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(sortKeys);
    if (v && typeof v === "object") {
      const out: Record<string, unknown> = {};
      for (const k of Object.keys(v as Record<string, unknown>).sort()) {
        out[k] = sortKeys((v as Record<string, unknown>)[k]);
      }
      return out;
    }
    return v;
  };
  return JSON.stringify(sortKeys(value));
}

describe("golden fold (TS ⇔ Python parity)", () => {
  it("folds mock_run.jsonl to mock_run.snapshot.json", () => {
    if (!existsSync(JSONL_PATH) || !existsSync(SNAPSHOT_PATH)) {
      throw new Error(
        "fixtures missing — run gateway tests first " +
          `(expected ${JSONL_PATH} and ${SNAPSHOT_PATH}; ` +
          "the gateway test suite generates them from engines/mock.py)",
      );
    }

    const frames: WireEvent[] = readFileSync(JSONL_PATH, "utf8")
      .split("\n")
      .filter((line) => line.trim().length > 0)
      .map((line) => JSON.parse(line) as WireEvent);
    expect(frames.length).toBeGreaterThan(0);

    const expected = JSON.parse(readFileSync(SNAPSHOT_PATH, "utf8")) as Snapshot;

    // run_id/engine/title are run-registry metadata, not event-derived — take them
    // from the snapshot; everything else must come out of the fold itself.
    const actual = foldEvents(frames, {
      run_id: expected.run_id,
      engine: expected.engine,
      title: expected.title,
    });

    expect(canonicalize(actual)).toEqual(canonicalize(expected));
    // byte-identical under sorted-keys serialization (CONTRACT §9)
    expect(stableStringify(actual)).toBe(stableStringify(expected));
  });
});
