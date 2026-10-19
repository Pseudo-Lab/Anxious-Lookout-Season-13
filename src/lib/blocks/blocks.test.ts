// #32 공용 golden fixture(backend/contracts/post-blocks-v1/golden.json) 기준 front 검사·정규화·편집기 왕복.
// 편집기 왕복은 실제 편집기 스키마(blockExtensions)로 ProseMirror 노드를 만들어 check()한 뒤 다시 블록으로 바꾼다.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { getSchema } from "@tiptap/core";
import { Node as PMNode } from "@tiptap/pm/model";
import { blockExtensions } from "@/components/blocks/editor/schema";
import { fromEditor, toEditor, type EditorNode } from "./adapter";
import { BLOCK_LIMITS, canonicalBytes, isAllowedLink, parseBlockDocument } from "./document";
import { formatChanges, hasVisibleContent, imageIds, textProjection } from "./projection";
import type { BlockDocument } from "./types";

interface Golden {
  contractVersion: string;
  requestContentPointer: string;
  valid: { name: string; input: unknown; canonical: BlockDocument; textProjection: string; imageIds: string[] }[];
  formatDiff: { name: string; base: BlockDocument; candidate: BlockDocument; baseTextProjection: string; candidateTextProjection: string; formatChanges: unknown[] }[];
  invalid: { name: string; input: unknown; stage: string; status: number; code: string; path: string }[];
  limits: Record<string, number>;
}
const golden: Golden = JSON.parse(readFileSync("backend/contracts/post-blocks-v1/golden.json", "utf8"));
const schema = getSchema(blockExtensions());

function canonical(input: unknown): BlockDocument {
  const r = parseBlockDocument(input);
  if (!r.ok) throw new Error(`unexpected ${r.error.code} at ${r.error.path}`);
  return r.doc;
}
/** 편집기 스키마를 통과시킨 뒤(노드 생성·check·toJSON) 다시 블록으로 바꾼다. */
function viaEditor(doc: BlockDocument): BlockDocument {
  const node = PMNode.fromJSON(schema, toEditor(doc));
  node.check();
  const back = fromEditor(node.toJSON() as EditorNode);
  if (!back.ok) throw new Error(`editor output rejected: ${back.error.code} at ${back.error.path}`);
  return back.doc;
}

const ID = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, "0")}`;
let seq = 0;
const nextId = () => ID(++seq);
const doc = (blocks: unknown[]) => ({ format: "post-blocks", version: 1, blocks });
const para = (runs: unknown[] = []) => ({ id: nextId(), type: "paragraph", runs });
const code = (text: string) => ({ id: nextId(), type: "code", text });

describe("golden contract", () => {
  it("is contract 0.3 and front limits match", () => {
    expect(golden.contractVersion).toBe("0.3");
    expect(BLOCK_LIMITS).toEqual(golden.limits);
  });

  describe.each(golden.valid)("valid: $name", (f) => {
    it("canonicalizes exactly", () => expect(canonical(f.input)).toEqual(f.canonical));
    it("is idempotent on canonical", () => expect(canonical(f.canonical)).toEqual(f.canonical));
    it("round-trips through the editor schema unchanged", () => expect(viaEditor(f.canonical)).toEqual(f.canonical));
    it("projects text and structural image ids exactly", () => {
      expect(textProjection(f.canonical)).toBe(f.textProjection);
      expect(imageIds(f.canonical)).toEqual(f.imageIds);
    });
  });

  describe.each(golden.formatDiff)("formatDiff: $name", (f) => {
    it("keeps format-only changes visible when text is equal", () => {
      expect([textProjection(f.base), textProjection(f.candidate)]).toEqual([f.baseTextProjection, f.candidateTextProjection]);
      expect(formatChanges(f.base, f.candidate)).toEqual(f.formatChanges);
    });
  });

  describe.each(golden.invalid)("invalid: $name ($stage)", (f) => {
    it(`rejects with ${f.code} at ${f.path}`, () => {
      const r = parseBlockDocument(f.input);
      expect(r.ok).toBe(false);
      if (r.ok) return;
      expect({ status: r.error.status, code: r.error.code, path: `${golden.requestContentPointer}${r.error.path}` }).toEqual({ status: f.status, code: f.code, path: f.path });
    });
  });
});

describe("limit boundaries (inclusive)", () => {
  const err = (input: unknown) => {
    const r = parseBlockDocument(input);
    return r.ok ? null : r.error.code;
  };

  it("text total 200000 code points", () => {
    const blocks = Array.from({ length: 10 }, () => para([{ text: "가".repeat(20000), marks: [] }]));
    expect(err(doc(blocks))).toBeNull();
    expect(err(doc([...blocks, para([{ text: "x", marks: [] }])]))).toBe("content_limit_exceeded");
  });

  it("total ids 2000 including items and paragraphs", () => {
    const items = Array.from({ length: 999 }, () => ({ id: nextId(), blocks: [para()] }));
    const list = { id: nextId(), type: "list", ordered: false, items };
    expect(err(doc([list, para()]))).toBeNull();
    expect(err(doc([list, para(), para()]))).toBe("content_limit_exceeded");
  });

  it("merged run 20000 code points is checked after merging", () => {
    expect(err(doc([para([{ text: "a".repeat(10000), marks: [] }, { text: "b".repeat(10000), marks: [] }])]))).toBeNull();
    expect(err(doc([para([{ text: "a".repeat(10000), marks: [] }, { text: "b".repeat(10001), marks: [] }])]))).toBe("content_limit_exceeded");
  });

  it("caption total 2000 code points", () => {
    const image = (n: number) => ({ id: nextId(), type: "image", imageId: ID(900), caption: [{ text: "a".repeat(1000), marks: ["bold"] }, { text: "b".repeat(n), marks: [] }] });
    expect(err(doc([image(1000)]))).toBeNull();
    expect(err(doc([image(1001)]))).toBe("content_limit_exceeded");
  });

  it("canonical UTF-8 document bytes 1 MiB → 413 above", () => {
    // 같은 mark의 run이 병합되지 않게 색을 번갈아 둔 run 10000개 + ASCII 코드 블록으로 정확한 크기를 맞춘다
    const runs = Array.from({ length: 10000 }, (_, i) => ({ text: "😀".repeat(10), marks: ["bold", "italic", { color: i % 2 ? "#000000" : "#FFFFFF" }] }));
    const p = para(runs);
    const base = canonical(doc([p, code("")]));
    const pad = BLOCK_LIMITS.documentBytes - canonicalBytes(base);
    expect(pad).toBeGreaterThan(0);
    const exact = doc([p, code("x".repeat(pad))]);
    expect(canonicalBytes(canonical(exact))).toBe(BLOCK_LIMITS.documentBytes);
    const r = parseBlockDocument(doc([p, code("x".repeat(pad + 1))]));
    expect(r.ok ? null : [r.error.status, r.error.code]).toEqual([413, "payload_too_large"]);
  });
});

describe("raw input checks (same order as the server)", () => {
  it("rejects a lone surrogate anywhere as invalid_block_document at the root", () => {
    const r = parseBlockDocument(doc([para([{ text: "a\ud800b", marks: [] }])]));
    expect(r.ok ? null : r.error).toEqual({ status: 422, code: "invalid_block_document", path: "" });
  });
  it("keeps a valid surrogate pair (one code point)", () => {
    expect(parseBlockDocument(doc([para([{ text: "😀", marks: [] }])])).ok).toBe(true);
  });
  it("bounds raw nesting before shape checks", () => {
    let deep: unknown = "x";
    for (let i = 0; i < 45; i++) deep = [deep];
    const r = parseBlockDocument({ ...doc([para()]), extra: deep });
    expect(r.ok ? null : r.error).toEqual({ status: 422, code: "content_limit_exceeded", path: "" });
  });
});

describe("links and visible content", () => {
  it.each([
    ["https://example.com/a", true],
    ["mailto:a@example.com", true],
    ["#section", true],
    ["#", false],
    ["https://user:pw@example.com", false],
    ["https://example.com/a b", false],
    ["https://example.com\\x", false],
    ["javascript:alert(1)", false],
    ["mailto:", false],
    ["data:text/html,x", false],
  ])("%s → %s", (url, ok) => expect(isAllowedLink(url)).toBe(ok));

  it("empty paragraphs are not visible content; an image alone is", () => {
    expect(hasVisibleContent(canonical(doc([para([{ text: "  \n", marks: [] }])])))).toBe(false);
    expect(hasVisibleContent(canonical(doc([{ id: nextId(), type: "image", imageId: ID(900) }])))).toBe(true);
  });

  it("moving a block keeps its id and reports a move, not remove+add", () => {
    const a = para([{ text: "A", marks: [] }]);
    const b = para([{ text: "B", marks: [] }]);
    const changes = formatChanges(canonical(doc([a, b])), canonical(doc([b, a])));
    expect(changes.map((c) => c.kind)).toEqual(["moved", "moved"]);
  });
});

describe("editor adapter details", () => {
  it("hard breaks without marks join the previous run; bold text across a break stays one run", () => {
    const editor: EditorNode = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          attrs: { id: ID(1), textAlign: "left" },
          content: [
            { type: "text", text: "a", marks: [{ type: "bold" }] },
            { type: "hardBreak" },
            { type: "text", text: "b", marks: [{ type: "bold" }] },
          ],
        },
      ],
    };
    const r = fromEditor(editor);
    expect(r.ok && r.doc.blocks[0]).toEqual({ id: ID(1), type: "paragraph", align: "left", runs: [{ text: "a\nb", marks: ["bold"] }] });
  });

  it("a marked line break run keeps its own marks", () => {
    const d = canonical(doc([{ id: ID(1), type: "paragraph", runs: [{ text: "x", marks: [] }, { text: "\n", marks: ["bold"] }] }]));
    expect(viaEditor(d)).toEqual(d);
  });

  it("an empty editor (one empty paragraph) is the canonical empty document", () => {
    const r = fromEditor({ type: "doc", content: [{ type: "paragraph", attrs: { id: ID(1), textAlign: "left" } }] });
    expect(r.ok && r.doc).toEqual({ format: "post-blocks", version: 1, blocks: [{ id: ID(1), type: "paragraph", runs: [], align: "left" }] });
  });

  it("merged editor cells are not dropped silently: the contract check rejects them", () => {
    const cell = (colspan: number) => ({ type: "tableCell", attrs: { id: nextId(), colspan, rowspan: 1, align: "left" }, content: [{ type: "paragraph", attrs: { id: nextId() } }] });
    const r = fromEditor({ type: "doc", content: [{ type: "table", attrs: { id: nextId() }, content: [{ type: "tableRow", attrs: { id: nextId() }, content: [cell(2)] }] }] });
    expect(r.ok ? null : r.error.path).toBe("/blocks/0/rows/0/cells/0/colspan");
  });
});
