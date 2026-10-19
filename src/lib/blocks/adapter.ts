// #32 post-blocks v1 <-> 편집기(Tiptap JSON) 변환. 편집기 내부 JSON은 저장하지 않는다.
// - toEditor: canonical 문서(parseBlockDocument 결과)를 편집기 내용으로. id·속성을 그대로 옮긴다.
// - fromEditor: 편집기 내용을 계약 형태로 옮긴 뒤 parseBlockDocument로 검사·정규화한다(run 병합·mark 순서·기본값).
// run.text의 LF는 편집기에서 hardBreak다. hardBreak에는 그 run의 marks를 실어 왕복해도 run 경계·서식이 바뀌지 않는다
// (편집기에서 새로 넣는 줄바꿈은 넣는 위치의 marks를 받는다, schema.ts PostHardBreak).

import { parseBlockDocument, type ParseResult } from "./document";
import {
  BLOCKS_FORMAT,
  BLOCKS_VERSION,
  type Align,
  type Block,
  type BlockDocument,
  type FlowBlock,
  type ListBlock,
  type Mark,
  type ParagraphBlock,
  type Run,
  type TableCell,
} from "./types";

export interface EditorNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: EditorNode[];
  marks?: { type: string; attrs?: Record<string, unknown> }[];
  text?: string;
}

// ---- 블록 → 편집기 ----

function editorMarks(run: Run): EditorNode["marks"] {
  const marks: NonNullable<EditorNode["marks"]> = [];
  for (const m of run.marks) {
    if (typeof m === "string") marks.push({ type: m });
    else marks.push({ type: "textStyle", attrs: { color: m.color } });
  }
  if (run.link) marks.push({ type: "link", attrs: { href: run.link } });
  return marks;
}

function inline(runs: Run[]): EditorNode[] {
  const out: EditorNode[] = [];
  for (const run of runs) {
    const marks = editorMarks(run);
    const withMarks = (n: EditorNode): EditorNode => (marks && marks.length ? { ...n, marks } : n);
    run.text.split("\n").forEach((part, i) => {
      if (i > 0) out.push(withMarks({ type: "hardBreak" }));
      if (part) out.push(withMarks({ type: "text", text: part }));
    });
  }
  return out;
}

const withContent = (node: EditorNode, content: EditorNode[]): EditorNode => (content.length ? { ...node, content } : node);

function paragraphNode(p: ParagraphBlock): EditorNode {
  return withContent({ type: "paragraph", attrs: { id: p.id, textAlign: p.align ?? "left" } }, inline(p.runs));
}

function listNode(l: ListBlock): EditorNode {
  return {
    type: l.ordered ? "orderedList" : "bulletList",
    attrs: l.ordered ? { id: l.id, start: l.start ?? 1 } : { id: l.id },
    content: l.items.map((item) => ({
      type: "listItem",
      attrs: { id: item.id },
      content: [paragraphNode(item.blocks[0]), ...(item.blocks[1] ? [listNode(item.blocks[1])] : [])],
    })),
  };
}

function blockNode(b: Block): EditorNode {
  switch (b.type) {
    case "paragraph":
      return paragraphNode(b);
    case "heading":
      return withContent({ type: "heading", attrs: { id: b.id, level: b.level, textAlign: b.align } }, inline(b.runs));
    case "quote":
      return { type: "blockquote", attrs: { id: b.id }, content: b.blocks.map(blockNode) };
    case "list":
      return listNode(b);
    case "divider":
      return { type: "horizontalRule", attrs: { id: b.id } };
    case "code":
      return withContent({ type: "codeBlock", attrs: { id: b.id, language: b.language } }, b.text ? [{ type: "text", text: b.text }] : []);
    case "image":
      return withContent({ type: "postImage", attrs: { id: b.id, imageId: b.imageId, alt: b.alt, widthPercent: b.widthPercent, align: b.align } }, inline(b.caption));
    case "embed":
      return withContent({ type: "embed", attrs: { id: b.id, provider: b.provider, resourceId: b.resourceId } }, inline(b.caption));
    case "table":
      return {
        type: "table",
        attrs: { id: b.id },
        content: b.rows.map((row) => ({
          type: "tableRow",
          attrs: { id: row.id },
          content: row.cells.map((cell) => ({
            type: row.header ? "tableHeader" : "tableCell",
            attrs: { id: cell.id, align: cell.align, background: cell.background ?? null },
            content: cell.blocks.map((p) => withContent({ type: "paragraph", attrs: { id: p.id, textAlign: "left" } }, inline(p.runs))),
          })),
        })),
      };
    case "columns":
      return {
        type: "columns",
        attrs: { id: b.id },
        content: b.columns.map((c) => ({ type: "column", attrs: { id: c.id, width: c.width }, content: c.blocks.map(blockNode) })),
      };
  }
}

/** canonical 문서 → 편집기 doc JSON. */
export function toEditor(doc: BlockDocument): EditorNode {
  return { type: "doc", content: doc.blocks.map(blockNode) };
}

// ---- 편집기 → 블록(검사 전 형태) ----

const attr = (n: EditorNode, k: string) => n.attrs?.[k];
const alignOf = (n: EditorNode, k = "textAlign"): Align => ((attr(n, k) as Align | null | undefined) ?? "left");

function runsOf(n: EditorNode): Run[] {
  const runs: Run[] = [];
  for (const c of n.content ?? []) {
    const marks: Mark[] = [];
    let link: string | undefined;
    for (const m of c.marks ?? []) {
      if (m.type === "link") link = String(m.attrs?.href ?? "");
      else if (m.type === "textStyle") {
        if (m.attrs?.color) marks.push({ color: String(m.attrs.color) });
      } else marks.push(m.type as Mark);
    }
    // 줄바꿈은 그 노드 자신의 marks·link를 가진 "\n" run이다(같은 서식의 인접 run은 검사 단계에서 합쳐진다)
    const text = c.type === "text" ? (c.text ?? "") : c.type === "hardBreak" ? "\n" : "";
    if (!text) continue;
    runs.push(link === undefined ? { text, marks } : { text, marks, link });
  }
  return runs;
}

function fromParagraph(n: EditorNode, inCell = false): ParagraphBlock {
  const p: ParagraphBlock = { id: String(attr(n, "id")), type: "paragraph", runs: runsOf(n) };
  if (!inCell) p.align = alignOf(n);
  return p;
}

function fromList(n: EditorNode): ListBlock {
  const ordered = n.type === "orderedList";
  const l: ListBlock = {
    id: String(attr(n, "id")),
    type: "list",
    ordered,
    items: (n.content ?? []).map((item) => {
      const [first, second] = item.content ?? [];
      const para = fromParagraph(first);
      return { id: String(attr(item, "id")), blocks: second ? [para, fromList(second)] : [para] };
    }),
  };
  if (ordered) l.start = Number(attr(n, "start") ?? 1);
  return l;
}

function fromNode(n: EditorNode): Block {
  const id = String(attr(n, "id"));
  switch (n.type) {
    case "paragraph":
      return fromParagraph(n);
    case "heading":
      return { id, type: "heading", level: Number(attr(n, "level")) as 1, runs: runsOf(n), align: alignOf(n) };
    case "blockquote":
      return { id, type: "quote", blocks: (n.content ?? []).map(fromNode) as (ParagraphBlock | ListBlock)[] };
    case "bulletList":
    case "orderedList":
      return fromList(n);
    case "horizontalRule":
      return { id, type: "divider" };
    case "codeBlock":
      return { id, type: "code", text: (n.content ?? []).map((c) => c.text ?? "").join(""), language: (attr(n, "language") as "plain") ?? "plain" };
    case "postImage":
      return {
        id,
        type: "image",
        imageId: String(attr(n, "imageId")),
        alt: String(attr(n, "alt") ?? ""),
        caption: runsOf(n),
        widthPercent: Number(attr(n, "widthPercent")) as 100,
        align: alignOf(n, "align"),
      };
    case "embed":
      return { id, type: "embed", provider: "youtube", resourceId: String(attr(n, "resourceId")), caption: runsOf(n) };
    case "table":
      return {
        id,
        type: "table",
        rows: (n.content ?? []).map((row) => {
          const cells = row.content ?? [];
          return {
            id: String(attr(row, "id")),
            header: cells.length > 0 && cells.every((c) => c.type === "tableHeader"),
            cells: cells.map((c) => {
              const cell: TableCell = { id: String(attr(c, "id")), blocks: (c.content ?? []).map((p) => fromParagraph(p, true)), align: alignOf(c, "align") };
              if (attr(c, "background")) cell.background = String(attr(c, "background"));
              // 병합·열 너비는 계약에 없다. 편집기가 막지만 그대로 내보내 서버와 같은 검사에서 거절되게 한다.
              if (attr(c, "colspan") !== 1 && attr(c, "colspan") !== undefined) (cell as unknown as Record<string, unknown>).colspan = attr(c, "colspan");
              if (attr(c, "rowspan") !== 1 && attr(c, "rowspan") !== undefined) (cell as unknown as Record<string, unknown>).rowspan = attr(c, "rowspan");
              return cell;
            }),
          };
        }),
      };
    case "columns":
      return {
        id,
        type: "columns",
        columns: (n.content ?? []).map((c) => ({ id: String(attr(c, "id")), width: Number(attr(c, "width")) as 1, blocks: (c.content ?? []).map(fromNode) as FlowBlock[] })),
      };
    default:
      // 알 수 없는 노드는 그대로 type을 남겨 검사에서 거절되게 한다(조용히 버리지 않는다)
      return { id, type: n.type } as unknown as Block;
  }
}

/** 편집기 doc JSON → 검사·정규화된 문서(또는 첫 오류). */
export function fromEditor(json: EditorNode): ParseResult {
  const raw = { format: BLOCKS_FORMAT, version: BLOCKS_VERSION, blocks: (json.content ?? []).map(fromNode) };
  return parseBlockDocument(raw);
}
