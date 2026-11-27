import { describe, expect, it } from "vitest";
import { outline } from "./toc";
import type { BlockDocument } from "./types";

const run = (text: string) => ({ text, marks: [] });

describe("outline", () => {
  it("collects top-level and column headings in order, skipping empty ones", () => {
    const doc = {
      blocks: [
        { id: "a", type: "heading", level: 2, runs: [run("시작 "), run(" 하며")], align: "left" },
        { id: "p", type: "paragraph", runs: [run("본문")] },
        {
          id: "c",
          type: "columns",
          columns: [{ id: "c1", width: 1, blocks: [{ id: "b", type: "heading", level: 3, runs: [run("왼쪽")], align: "left" }] }],
        },
        { id: "e", type: "heading", level: 2, runs: [run("   ")], align: "left" },
      ],
    } as unknown as BlockDocument;
    expect(outline(doc)).toEqual([
      { id: "a", level: 2, text: "시작 하며" },
      { id: "b", level: 3, text: "왼쪽" },
    ]);
  });
});
