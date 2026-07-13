import { describe, expect, it } from "vitest";
import { lineDiff } from "../lib/diff";

describe("lineDiff (LCS)", () => {
  it("identical texts are all 'same'", () => {
    expect(lineDiff("a\nb", "a\nb")).toEqual([
      { type: "same", text: "a" },
      { type: "same", text: "b" },
    ]);
  });

  it("marks additions and deletions around a common core", () => {
    const d = lineDiff("keep\nold line\ntail", "keep\nnew line\ntail");
    expect(d).toEqual([
      { type: "same", text: "keep" },
      { type: "del", text: "old line" },
      { type: "add", text: "new line" },
      { type: "same", text: "tail" },
    ]);
  });

  it("handles pure insertion and pure deletion", () => {
    expect(lineDiff("a", "a\nb")).toEqual([
      { type: "same", text: "a" },
      { type: "add", text: "b" },
    ]);
    expect(lineDiff("a\nb", "b")).toEqual([
      { type: "del", text: "a" },
      { type: "same", text: "b" },
    ]);
  });

  it("accounts for every line on both sides and keeps a common subsequence", () => {
    const d = lineDiff("x\ncommon\ny", "y\ncommon\nx");
    // non-add lines replay the old text; non-del lines replay the new text
    expect(d.filter((l) => l.type !== "add").map((l) => l.text)).toEqual(["x", "common", "y"]);
    expect(d.filter((l) => l.type !== "del").map((l) => l.text)).toEqual(["y", "common", "x"]);
    expect(d.filter((l) => l.type === "same")).toHaveLength(1); // LCS length is 1 here
  });

  it("empty-vs-content diffs cleanly", () => {
    expect(lineDiff("", "a")).toEqual([
      { type: "del", text: "" },
      { type: "add", text: "a" },
    ]);
  });
});
