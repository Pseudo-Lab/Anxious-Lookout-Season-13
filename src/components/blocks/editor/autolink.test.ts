// #80 F3: 편집기 Link 확장의 autolink를 실제 Tiptap 플러그인 그대로(DOM 없이 EditorState로) 돌려 본다.
// 브라우저 입력(한글 조합 포함)은 e2e·사람 확인 몫이고, 여기서는 "어떤 글자가 링크가 되는가"만 고정한다.
import { describe, expect, it } from "vitest";
import { getExtensionField, getSchema, type AnyExtension } from "@tiptap/core";
import { Node as PMNode, type Mark } from "@tiptap/pm/model";
import { EditorState, TextSelection, type Plugin } from "@tiptap/pm/state";
import { fromEditor, type EditorNode } from "@/lib/blocks/adapter";
import { blockExtensions } from "./schema";

const extensions = blockExtensions();
const schema = getSchema(extensions);
const link = extensions.find((e) => e.name === "link") as AnyExtension;
const addPlugins = getExtensionField<() => Plugin[]>(link, "addProseMirrorPlugins", {
  name: link.name,
  options: link.options,
  storage: link.storage,
  editor: {} as never,
  type: schema.marks.link,
} as never);
const plugins = addPlugins();

const IMAGE = "00000000-0000-4000-8000-000000000001";
// 블록 id는 편집기에서 UniqueID가 붙인다. 여기서는 저장 변환(fromEditor)이 받도록 직접 넣는다.
const BLOCK = "00000000-0000-4000-8000-000000000002";
const blockOf = (kind: "paragraph" | "codeBlock" | "caption") =>
  kind === "paragraph"
    ? { type: "paragraph", attrs: { id: BLOCK } }
    : kind === "codeBlock"
      ? { type: "codeBlock", attrs: { id: BLOCK, language: "plain" } }
      : { type: "postImage", attrs: { id: BLOCK, imageId: IMAGE, alt: "" } };

/** 빈 블록 하나에 글자를 차례로 친다(각 조각이 한 트랜잭션 = 키 입력 단위). marks는 그 조각에 걸 mark 이름. */
function type(chunks: (string | { text: string; marks: string[] })[], kind: "paragraph" | "codeBlock" | "caption" = "paragraph") {
  const doc = PMNode.fromJSON(schema, { type: "doc", content: [blockOf(kind)] });
  let state = EditorState.create({ schema, doc, plugins, selection: TextSelection.create(doc, 1) });
  for (const chunk of chunks) {
    const { text, marks } = typeof chunk === "string" ? { text: chunk, marks: [] } : chunk;
    const tr = state.tr.insertText(text);
    const from = state.selection.from;
    for (const m of marks) tr.addMark(from, from + text.length, schema.marks[m].create());
    tr.setStoredMarks([]);
    state = state.apply(tr);
  }
  return state;
}

/** 블록 안 글자를 link href가 같은 구간끼리 묶는다. */
function linked(state: EditorState): { text: string; href: string | null }[] {
  const out: { text: string; href: string | null }[] = [];
  state.doc.firstChild!.forEach((n) => {
    const href = (n.marks.find((m: Mark) => m.type.name === "link")?.attrs.href as string | undefined) ?? null;
    const last = out[out.length - 1];
    if (last && last.href === href) last.text += n.text ?? "";
    else out.push({ text: n.text ?? "", href });
  });
  return out;
}

describe("#80 F3 autolink", () => {
  it("a typed https URL becomes a link at the space, and only the URL is linked", () => {
    const s = type(["https://example.com/a?b=1", " ", "다음 글자"]);
    expect(linked(s)).toEqual([
      { text: "https://example.com/a?b=1", href: "https://example.com/a?b=1" },
      { text: " 다음 글자", href: null },
    ]);
  });

  it("http and mailto links are made too", () => {
    expect(linked(type(["http://example.com", " "]))[0].href).toBe("http://example.com");
    expect(linked(type(["mailto:a@example.com", " "]))[0].href).toBe("mailto:a@example.com");
  });

  it("Enter also ends the URL (split block)", () => {
    let s = type(["https://example.com"]);
    s = s.apply(s.tr.split(s.selection.from));
    expect(linked(s)).toEqual([{ text: "https://example.com", href: "https://example.com" }]);
  });

  it.each([
    "javascript:alert(1)",
    "https://user:pw@example.com",
    "user:pw@example.com",
    "example.com",
    "main.py",
    "README.md",
    "a@example.com",
    "data:text/html,x",
  ])("%s stays plain text", (word) => {
    expect(linked(type([word, " "]))).toEqual([{ text: `${word} `, href: null }]);
  });

  it("Hangul glued to a URL is not linked (the word is not a whole link)", () => {
    expect(linked(type(["한글https://example.com", " "]))).toEqual([{ text: "한글https://example.com ", href: null }]);
  });

  it("Hangul typed after a link is not linked and the link keeps its range", () => {
    const s = type(["https://example.com", " ", "가", "나", "다"]);
    expect(linked(s)).toEqual([
      { text: "https://example.com", href: "https://example.com" },
      { text: " 가나다", href: null },
    ]);
  });

  it("no link inside inline code, a code block, or an image caption", () => {
    expect(linked(type([{ text: "https://example.com", marks: ["code"] }, " "])).every((r) => r.href === null)).toBe(true);
    expect(linked(type(["https://example.com", " "], "codeBlock"))).toEqual([{ text: "https://example.com ", href: null }]);
    expect(linked(type(["https://example.com", " "], "caption"))).toEqual([{ text: "https://example.com ", href: null }]);
  });

  it("an autolinked paragraph saves as a contract link mark", () => {
    const s = type(["https://example.com", " ", "끝"]);
    const r = fromEditor(s.doc.toJSON() as EditorNode);
    expect(r).toMatchObject({ ok: true });
    if (!r.ok) return;
    const block = r.doc.blocks[0];
    expect(block.type).toBe("paragraph");
    expect(block.type === "paragraph" && block.runs).toEqual([
      { text: "https://example.com", marks: [], link: "https://example.com" },
      { text: " 끝", marks: [] },
    ]);
  });
});
