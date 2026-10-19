import { Extension, Node, mergeAttributes, type Extensions } from "@tiptap/core";
import { NodeSelection, Plugin, PluginKey, TextSelection, type Transaction } from "@tiptap/pm/state";
import type { Node as PMNode } from "@tiptap/pm/model";
import Document from "@tiptap/extension-document";
import Paragraph from "@tiptap/extension-paragraph";
import Text from "@tiptap/extension-text";
import Heading from "@tiptap/extension-heading";
import Blockquote from "@tiptap/extension-blockquote";
import Bold from "@tiptap/extension-bold";
import Italic from "@tiptap/extension-italic";
import Underline from "@tiptap/extension-underline";
import Strike from "@tiptap/extension-strike";
import Code from "@tiptap/extension-code";
import CodeBlock from "@tiptap/extension-code-block";
import HorizontalRule from "@tiptap/extension-horizontal-rule";
import HardBreak from "@tiptap/extension-hard-break";
import Link from "@tiptap/extension-link";
import { BulletList, ListItem, ListKeymap, OrderedList } from "@tiptap/extension-list";
import { Dropcursor, Gapcursor, Placeholder, UndoRedo } from "@tiptap/extensions";
import { TextStyle } from "@tiptap/extension-text-style";
import TextAlign from "@tiptap/extension-text-align";
import { Table, TableCell, TableHeader, TableRow, createTable } from "@tiptap/extension-table";
import UniqueID from "@tiptap/extension-unique-id";
import { uuidV4 } from "@/lib/research/idempotency";
import { CODE_LANGUAGES, COLUMN_WEIGHT, FONT_SIZES, IMAGE_WIDTH, LIMITS, YOUTUBE_ID, ptToPx, type Align } from "@/lib/blocks/types";
import { isAllowedLink } from "@/lib/blocks/document";
import { ownImageUrl } from "@/lib/posts/api";

// #32 편집기 스키마. 저장 계약(post-blocks v1)이 허용하는 구조만 편집기 안에서도 만들 수 있게 노드 content 식으로 제한한다.
// - group "flow": columns 안에 둘 수 있는 블록(columns·table 제외). "block"은 문서 최상위.
// - 계약 밖 구조(표 병합·열 너비·중간 header 행·목록 4단 이상)는 문서 검사(contractGuard)에서 트랜잭션을 거절한다.
// - 외부 HTML을 붙여 넣어도 여기 정의된 parse 규칙에 맞는 것만 남는다(iframe·외부 img·style 문자열은 들어오지 않는다).

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ALIGNS: Align[] = ["left", "center", "right"];
const LIST_TYPES = new Set(["bulletList", "orderedList"]);
const CELL_TYPES = new Set(["tableCell", "tableHeader"]);

/** 계약 색상(#RRGGBB 대문자). 붙여 넣은 rgb()도 같은 값이면 받아들이고 나머지 CSS 값은 버린다. */
export function normalizeHex(value: string | null | undefined): string | null {
  if (!value) return null;
  const v = value.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toUpperCase();
  const m = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/.exec(v);
  if (!m) return null;
  const parts = m.slice(1).map(Number);
  if (parts.some((n) => n > 255)) return null;
  return `#${parts.map((n) => n.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}

export { isAllowedLink };

/** watch?v=, youtu.be/, shorts/, embed/ 주소나 ID 자체에서 11자 영상 ID만 꺼낸다. 시작 시각 등 나머지는 버린다(v1 미지원). */
export function youtubeIdFrom(input: string): string | null {
  const s = input.trim();
  if (YOUTUBE_ID.test(s)) return s;
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  const host = u.hostname.replace(/^(www|m)\./, "");
  let id: string | null = null;
  if (host === "youtu.be") id = u.pathname.split("/")[1] ?? null;
  else if (host === "youtube.com" || host === "youtube-nocookie.com") {
    if (u.pathname === "/watch") id = u.searchParams.get("v");
    else {
      const m = /^\/(shorts|embed|live)\/([^/]+)/.exec(u.pathname);
      id = m ? m[2] : null;
    }
  }
  return id && YOUTUBE_ID.test(id) ? id : null;
}

const alignAttr = (fallback: Align) => ({
  default: fallback,
  parseHTML: (el: HTMLElement) => {
    const v = el.getAttribute("data-align");
    return ALIGNS.includes(v as Align) ? v : fallback;
  },
  renderHTML: (attrs: Record<string, unknown>) => ({ "data-align": attrs.align }),
});

const intAttr = (name: string, allowed: readonly number[], fallback: number) => ({
  default: fallback,
  parseHTML: (el: HTMLElement) => {
    const v = Number(el.getAttribute(`data-${name}`));
    return allowed.includes(v) ? v : fallback;
  },
  renderHTML: (attrs: Record<string, unknown>) => ({ [`data-${name}`]: attrs[name] }),
});

// ---- 텍스트 색(textStyle.color)과 글자 크기(textStyle.fontSize, #36 pt). 계약 hex·허용 pt만 저장한다. ----
// 글자 크기는 이 편집기가 쓴 HTML(data-font-size)에서만 읽는다. 다른 곳에서 붙여 넣은 글자 크기는 가져오지 않는다.
const isFontSize = (v: unknown): v is number => (FONT_SIZES as readonly number[]).includes(v as number);
const TextColor = Extension.create({
  name: "textColor",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          color: {
            default: null,
            parseHTML: (el) => normalizeHex(el.style.color),
            renderHTML: (attrs) => (attrs.color ? { style: `color: ${attrs.color}` } : {}),
          },
          fontSize: {
            default: null,
            parseHTML: (el) => {
              const v = Number(el.getAttribute("data-font-size"));
              return isFontSize(v) ? v : null;
            },
            renderHTML: (attrs) => (isFontSize(attrs.fontSize) ? { "data-font-size": attrs.fontSize, style: `font-size: ${ptToPx(attrs.fontSize)}px` } : {}),
          },
        },
      },
    ];
  },
  addCommands() {
    return {
      setTextColor:
        (color: string) =>
        ({ chain }) => {
          const hex = normalizeHex(color);
          return hex ? chain().setMark("textStyle", { color: hex }).run() : false;
        },
      unsetTextColor:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { color: null }).removeEmptyTextStyle().run(),
      setTextSize:
        (pt: number) =>
        ({ chain }) =>
          isFontSize(pt) && chain().setMark("textStyle", { fontSize: pt }).run(),
      unsetTextSize:
        () =>
        ({ chain }) =>
          chain().setMark("textStyle", { fontSize: null }).removeEmptyTextStyle().run(),
    };
  },
});

// ---- 문단 안 줄바꿈(run.text의 LF). 줄바꿈 노드에 지금 위치의 marks를 그대로 싣는다. ----
// adapter는 줄바꿈을 그 노드의 marks를 가진 "\n" run으로 옮기므로(앞 run에 붙이지 않는다), 굵은 글자 안에서 넣은 줄바꿈은
// 굵은 run 안에 남고, 서식 없는 줄바꿈은 서식 없이 남는다. 이미지·영상 설명 안에서도 줄바꿈을 넣을 수 있다(계약 caption LF).
const PostHardBreak = HardBreak.extend({
  addCommands() {
    return {
      setHardBreak:
        () =>
        ({ state, tr, dispatch, commands }) => {
          const { selection, storedMarks, schema } = state;
          if (selection.$from.parent.type.spec.code) return commands.exitCode();
          // 링크는 줄바꿈으로 이어지지 않는다(inclusive=false와 같게)
          const marks = (storedMarks ?? selection.$from.marks()).filter((m) => m.type.name !== "link");
          if (dispatch) {
            tr.replaceSelectionWith(schema.nodes.hardBreak.create(null, null, marks), false);
            tr.setStoredMarks(marks);
            tr.scrollIntoView();
          }
          return true;
        },
    };
  },
});

// ---- 단 배치(columns > column). 2~3단, width는 1~4 가중치. ----
const Columns = Node.create({
  name: "columns",
  group: "block",
  content: "column{2,3}",
  isolating: true,
  defining: true,
  parseHTML: () => [{ tag: 'div[data-type="columns"]' }],
  renderHTML: ({ HTMLAttributes }) => ["div", mergeAttributes(HTMLAttributes, { "data-type": "columns", class: "pb-columns" }), 0],
  addCommands() {
    return {
      insertColumns:
        (widths: number[]) =>
        ({ state, tr, dispatch }) => {
          const { schema } = state;
          const node = schema.nodes.columns.create(
            null,
            widths.map((width) => schema.nodes.column.create({ width }, schema.nodes.paragraph.create()))
          );
          if (dispatch) {
            tr.replaceSelectionWith(node);
            // 넣은 뒤 커서를 첫 단의 문단으로 옮긴다(columns·column·paragraph 여는 위치 다음)
            let at = -1;
            tr.doc.descendants((n, pos) => {
              if (at !== -1) return false;
              if (n === node) at = pos;
              return at === -1;
            });
            if (at !== -1) tr.setSelection(TextSelection.create(tr.doc, at + 3));
            tr.scrollIntoView();
          }
          return true;
        },
      setColumnWidths:
        (widths: number[]) =>
        ({ state, tr, dispatch }) => {
          const { $from } = state.selection;
          for (let d = $from.depth; d > 0; d--) {
            const node = $from.node(d);
            if (node.type.name !== "columns") continue;
            if (node.childCount !== widths.length) return false;
            if (dispatch) {
              const start = $from.before(d) + 1;
              let pos = start;
              node.forEach((col, _o, i) => {
                tr.setNodeMarkup(pos, undefined, { ...col.attrs, width: widths[i] });
                pos += col.nodeSize;
              });
            }
            return true;
          }
          return false;
        },
    };
  },
});

const Column = Node.create({
  name: "column",
  content: "flow+",
  isolating: true,
  addAttributes: () => ({ width: intAttr("width", [1, 2, 3, 4], 1) }),
  parseHTML: () => [{ tag: 'div[data-type="column"]' }],
  renderHTML: ({ node, HTMLAttributes }) => [
    "div",
    // width는 1~4 정수만 parse되므로 style 값은 숫자다
    mergeAttributes(HTMLAttributes, { "data-type": "column", class: "pb-column", style: `flex-grow: ${node.attrs.width}` }),
    0,
  ],
});

// ---- 자체 업로드 이미지. URL이 아니라 imageId만 저장한다. caption은 링크 없는 runs. ----
export const PostImage = Node.create({
  name: "postImage",
  group: "block flow",
  content: "inline*",
  marks: "bold italic underline strike code textStyle",
  isolating: true,
  draggable: true,
  addAttributes: () => ({
    imageId: {
      default: null,
      parseHTML: (el) => {
        const v = el.getAttribute("data-image-id");
        return v && UUID.test(v) ? v.toLowerCase() : null;
      },
      renderHTML: (attrs) => ({ "data-image-id": attrs.imageId }),
    },
    alt: {
      default: "",
      parseHTML: (el) => (el.getAttribute("data-alt") ?? "").replace(/[\r\n]+/g, " "),
      renderHTML: (attrs) => ({ "data-alt": attrs.alt }),
    },
    widthPercent: {
      default: 100,
      parseHTML: (el: HTMLElement) => {
        const v = Number(el.getAttribute("data-width-percent"));
        return Number.isInteger(v) && v >= IMAGE_WIDTH.min && v <= IMAGE_WIDTH.max ? v : 100;
      },
      renderHTML: (attrs: Record<string, unknown>) => ({ "data-width-percent": attrs.widthPercent }),
    },
    align: alignAttr("center"),
  }),
  parseHTML: () => [{ tag: 'figure[data-type="post-image"]', getAttrs: (el) => (UUID.test(el.getAttribute("data-image-id") ?? "") ? null : false), contentElement: "figcaption" }],
  renderHTML: ({ node, HTMLAttributes }) => [
    "figure",
    mergeAttributes(HTMLAttributes, { "data-type": "post-image" }),
    ["img", { src: ownImageUrl(node.attrs.imageId), alt: node.attrs.alt }],
    ["figcaption", 0],
  ],
  addCommands() {
    return {
      insertPostImage:
        (attrs: { imageId: string; alt: string }, at?: number) =>
        ({ commands }) => {
          const node = { type: "postImage", attrs: { ...attrs, widthPercent: 100, align: "center" } };
          return at === undefined ? commands.insertContent(node) : commands.insertContentAt(at, node);
        },
    };
  },
});

// ---- 승인 provider(v1 YouTube)의 영상. 저장은 provider+resourceId만, 편집기·공개 모두 활성화 전 외부 요청 없음. ----
export const Embed = Node.create({
  name: "embed",
  group: "block flow",
  content: "inline*",
  marks: "bold italic underline strike code textStyle",
  isolating: true,
  draggable: true,
  addAttributes: () => ({
    provider: { default: "youtube", parseHTML: () => "youtube", renderHTML: () => ({ "data-provider": "youtube" }) },
    resourceId: {
      default: null,
      parseHTML: (el) => {
        const v = el.getAttribute("data-resource-id");
        return v && YOUTUBE_ID.test(v) ? v : null;
      },
      renderHTML: (attrs) => ({ "data-resource-id": attrs.resourceId }),
    },
  }),
  parseHTML: () => [{ tag: 'figure[data-type="embed"]', getAttrs: (el) => (YOUTUBE_ID.test(el.getAttribute("data-resource-id") ?? "") ? null : false), contentElement: "figcaption" }],
  // 정적 HTML(복사 등)에는 iframe 없이 텍스트 카드만 둔다
  renderHTML: ({ node, HTMLAttributes }) => [
    "figure",
    mergeAttributes(HTMLAttributes, { "data-type": "embed" }),
    ["div", { class: "pb-embed-card" }, `YouTube 동영상 (${node.attrs.resourceId})`],
    ["figcaption", 0],
  ],
  addCommands() {
    return {
      insertYouTube:
        (resourceId: string) =>
        ({ commands }) =>
          YOUTUBE_ID.test(resourceId) && commands.insertContent({ type: "embed", attrs: { provider: "youtube", resourceId } }),
    };
  },
});

// ---- 표 셀: paragraph만, 정렬·단색 배경은 셀 속성. 병합·열 너비는 쓰지 않는다. ----
const cellAttrs = {
  align: alignAttr("left"),
  background: {
    default: null,
    parseHTML: (el: HTMLElement) => normalizeHex(el.getAttribute("data-background")),
    renderHTML: (attrs: Record<string, unknown>) =>
      attrs.background ? { "data-background": attrs.background, style: `background-color: ${attrs.background}` } : {},
  },
};
const PostTableCell = TableCell.extend({
  content: "paragraph+",
  addAttributes() {
    return { ...this.parent?.(), ...cellAttrs };
  },
});
const PostTableHeader = TableHeader.extend({
  content: "paragraph+",
  addAttributes() {
    return { ...this.parent?.(), ...cellAttrs };
  },
});

// ---- 표: 열 폭은 표 속성 columnWidths(열별 정수 가중치, #36). 생략(null)이면 균등. 셀 colwidth(픽셀)는 쓰지 않는다. ----
const isWeights = (v: unknown): v is number[] =>
  Array.isArray(v) && v.length > 0 && v.length <= LIMITS.tableCols && v.every((w) => Number.isInteger(w) && w >= COLUMN_WEIGHT.min && w <= COLUMN_WEIGHT.max);
// #36 B2: 표는 커서가 있는 최상위 블록 바로 뒤에 넣는다(빈 문단이면 그 자리를 바꾼다). 커서는 첫 칸으로 간다.
// 기본 insertTable(replaceSelectionWith)은 글줄 처음에서는 앞에, 글줄 중간에서는 문단을 둘로 나눠 넣는다.
// 표는 문서 바로 아래에만 올 수 있으므로(단·표 안 금지) 그 안에서는 넣지 않는다.
const PostTable = Table.extend({
  addCommands() {
    return {
      ...this.parent?.(),
      insertTableAfterBlock:
        ({ rows = 3, cols = 3, withHeaderRow = true } = {}) =>
        ({ state, tr, dispatch }) => {
          const { selection } = state;
          const { $from } = selection;
          for (let d = $from.depth; d > 0; d--) if (["table", "columns"].includes($from.node(d).type.name)) return false;
          const table = createTable(state.schema, rows, cols, withHeaderRow);
          // gapcursor·블록 선택(이미지 등)은 그 뒤, 글줄은 그 최상위 블록 뒤, 빈 문단은 그 자리
          const block = $from.depth > 0 ? $from.node(1) : null;
          const replace = !!block && block.type.name === "paragraph" && block.content.size === 0;
          const at = !block ? selection.to : replace ? $from.before(1) : $from.after(1);
          if (dispatch) {
            if (replace) tr.replaceWith(at, at + block.nodeSize, table);
            else tr.insert(at, table);
            tr.setSelection(TextSelection.near(tr.doc.resolve(at + 1))).scrollIntoView();
          }
          return true;
        },
    };
  },
  addAttributes() {
    return {
      ...this.parent?.(),
      columnWidths: {
        default: null,
        parseHTML: (el: HTMLElement) => {
          try {
            const v: unknown = JSON.parse(el.getAttribute("data-column-widths") ?? "null");
            return isWeights(v) ? v : null;
          } catch {
            return null;
          }
        },
        renderHTML: (attrs: Record<string, unknown>) => (isWeights(attrs.columnWidths) ? { "data-column-widths": JSON.stringify(attrs.columnWidths) } : {}),
      },
    };
  },
});

/** 표의 열 수(첫 행 셀 수). */
export const tableColumnCount = (table: PMNode) => (table.childCount ? table.child(0).childCount : 0);

/** 목록 중첩 깊이(최상위 목록 = 1). */
export function maxListDepth(node: PMNode, depth = 0): number {
  let max = depth;
  node.forEach((child) => {
    const d = LIST_TYPES.has(child.type.name) ? depth + 1 : depth;
    max = Math.max(max, maxListDepth(child, d));
  });
  return max;
}

/** 계약에 없는 표 구조: 병합 셀, 열 너비, 첫 행이 아닌 header 행, 한 행 안 header/일반 셀 혼합, 직사각형이 아닌 표. */
export function tableViolation(table: PMNode): string | null {
  let cols = -1;
  let problem: string | null = null;
  table.forEach((row, _o, r) => {
    if (problem) return;
    let headers = 0;
    row.forEach((cell) => {
      if (cell.attrs.colspan !== 1 || cell.attrs.rowspan !== 1) problem = "merged";
      if (cell.attrs.colwidth) problem = problem ?? "colwidth";
      if (cell.type.name === "tableHeader") headers++;
    });
    if (headers !== 0 && headers !== row.childCount) problem = problem ?? "mixed-header";
    if (headers !== 0 && r !== 0) problem = problem ?? "header-row";
    if (cols !== -1 && row.childCount !== cols) problem = problem ?? "ragged";
    cols = row.childCount;
  });
  if (!problem && (table.childCount > LIMITS.tableRows || cols > LIMITS.tableCols)) problem = "size";
  return problem;
}

export const guardKey = new PluginKey("pb-contract-guard");

// 계약 밖 구조를 만드는 트랜잭션은 적용하지 않는다(조용히 고치지 않는다). 거절 사유는 onReject로 알린다.
const ContractGuard = Extension.create<{ onReject: (reason: string) => void }>({
  name: "contractGuard",
  addOptions: () => ({ onReject: () => {} }),
  addProseMirrorPlugins() {
    const { onReject } = this.options;
    return [
      new Plugin({
        key: guardKey,
        filterTransaction: (tr) => {
          if (!tr.docChanged) return true;
          let reason: string | null = null;
          if (maxListDepth(tr.doc) > LIMITS.listDepth) reason = "list-depth";
          tr.doc.descendants((node) => {
            if (reason) return false;
            if (node.type.name === "table") {
              reason = tableViolation(node);
              return false;
            }
            return true;
          });
          if (reason) onReject(reason);
          return reason === null;
        },
        // 표 칸 안 문단 정렬은 계약에 없다(cell.align만 쓴다). 붙여넣기·단축키로 생긴 문단 정렬은 기본값으로 되돌린다.
        appendTransaction: (trs, _old, state) => {
          if (!trs.some((t) => t.docChanged)) return null;
          const tr = state.tr;
          state.doc.descendants((node, pos, parent) => {
            if (node.type.name === "paragraph" && parent && CELL_TYPES.has(parent.type.name) && node.attrs.textAlign !== "left") {
              tr.setNodeMarkup(pos, undefined, { ...node.attrs, textAlign: "left" });
            }
            // 열 폭 가중치가 열 수와 맞지 않게 된 표(열을 넣고 뺀 명령이 가중치를 고치지 않은 경우)는 균등으로 되돌린다.
            // 열 추가·삭제 도구는 가중치를 같이 고치므로, 이것은 다른 경로(붙여넣기 등)의 안전장치다.
            if (node.type.name === "table" && node.attrs.columnWidths && node.attrs.columnWidths.length !== tableColumnCount(node)) {
              tr.setNodeMarkup(pos, undefined, { ...node.attrs, columnWidths: null });
            }
            return node.type.name !== "paragraph";
          });
          return tr.docChanged ? tr : null;
        },
      }),
    ];
  },
});

// 목록 Tab 들여쓰기·블록 이동 등 직접 넣는 키는 한글 조합 중에는 처리하지 않는다(IME가 Enter/Tab을 소비하게 둔다).
const ImeSafeKeys = Extension.create({
  name: "imeSafeKeys",
  priority: 1000,
  addKeyboardShortcuts() {
    const composing = () => this.editor.view.composing;
    return {
      "Mod-Alt-ArrowUp": () => !composing() && this.editor.commands.moveBlock(-1),
      "Mod-Alt-ArrowDown": () => !composing() && this.editor.commands.moveBlock(1),
    };
  },
});

// #36 B1: 목록 Backspace. ListKeymap은 목록 바로 뒤 문단을 마지막 항목 안으로 옮기는데, 이 계약의 항목은
// "문단 하나 + 하위 목록"이라 두 번째 문단이 들어갈 수 없어 새 빈 항목(표식)으로 감싸져 남았다.
// - 항목 첫 글자에서: 항목을 한 단계 해제한다(맨 위 단계면 문단으로).
// - 목록 바로 뒤 문단의 첫 글자에서: 목록의 마지막 글줄(가장 깊은 마지막 항목)에 합친다. 빈 문단이면 지우기만 한다.
//   그 뒤에 같은 종류 목록이 이어지면(가운데 항목을 해제했던 경우) 두 목록을 다시 하나로 잇는다.
// 표 칸·단·인용 안 목록도 같은 부모 안에서 똑같이 동작한다. 한글 조합 중에는 처리하지 않는다.
const ListBackspace = Extension.create({
  name: "listBackspace",
  priority: 1000,
  addKeyboardShortcuts() {
    const run = () => {
      const editor = this.editor;
      if (editor.view.composing) return false;
      if (editor.commands.undoInputRule()) return true;
      const { state } = editor;
      const { selection } = state;
      if (!selection.empty || !(selection instanceof TextSelection)) return false;
      const { $from } = selection;
      if ($from.parentOffset !== 0 || $from.parent.type.name !== "paragraph" || $from.depth < 1) return false;
      const holder = $from.node($from.depth - 1);
      if (holder.type.name === "listItem") {
        if ($from.index($from.depth - 1) !== 0) return false;
        return editor.commands.liftListItem("listItem");
      }
      const index = $from.index($from.depth - 1);
      const prev = index > 0 ? holder.child(index - 1) : null;
      if (!prev || !LIST_TYPES.has(prev.type.name)) return false;
      // 앞 목록의 마지막 글줄 끝 위치
      const paraStart = $from.before();
      const listStart = paraStart - prev.nodeSize;
      let end = -1;
      prev.descendants((node, pos) => {
        if (node.isTextblock) end = listStart + 1 + pos + 1 + node.content.size;
      });
      if (end < 0) return false;
      const para = $from.parent;
      const tr = state.tr.delete(paraStart, paraStart + para.nodeSize);
      // 가운데 항목을 해제했다가 합친 경우처럼 같은 종류 목록이 다시 맞닿으면 하나로 잇는다(앞 목록 안 위치는 그대로다)
      const next = index + 1 < holder.childCount ? holder.child(index + 1) : null;
      if (next && next.type === prev.type) tr.join(paraStart);
      if (para.content.size) tr.insert(end, para.content);
      tr.setSelection(TextSelection.create(tr.doc, end)).scrollIntoView();
      editor.view.dispatch(tr);
      return true;
    };
    return { Backspace: run, "Mod-Backspace": run };
  },
});

// 블록 이동: 커서가 있는 가장 가까운 "이동 단위"(목록 항목, 또는 문서·단·인용 안의 블록)를 같은 부모 안에서 한 칸 옮긴다.
// 모바일·키보드 사용자를 위한 이동 수단이다(드래그 핸들은 터치에서 불안정하다).
const MoveBlock = Extension.create({
  name: "moveBlock",
  addCommands() {
    return {
      moveBlock:
        (dir: -1 | 1) =>
        ({ state, tr, dispatch }) => {
          const { $from } = state.selection;
          const containers = new Set(["doc", "column", "blockquote", "bulletList", "orderedList"]);
          for (let d = $from.depth; d > 0; d--) {
            const parent = $from.node(d - 1);
            if (!containers.has(parent.type.name)) continue;
            const index = $from.index(d - 1);
            const target = index + dir;
            if (target < 0 || target >= parent.childCount) return false;
            if (dispatch) {
              const node = $from.node(d);
              const from = $from.before(d);
              const other = parent.child(target);
              const offset = $from.pos - from;
              tr.delete(from, from + node.nodeSize);
              // 아래로: 지운 뒤 다음 형제 뒤에 넣는다. 위로: 이전 형제 앞에 넣는다.
              const at = dir === 1 ? from + other.nodeSize : from - other.nodeSize;
              tr.insert(at, node);
              tr.setSelection(textSelectionNear(tr, at + offset));
              tr.scrollIntoView();
            }
            return true;
          }
          return false;
        },
      // #36 F4: pos에서 시작하는 블록(이미지·영상 등)을 같은 부모(문서·단·인용·목록) 안에서 한 칸 옮기고 그 블록을 선택한다.
      // 블록을 선택한 상태(NodeSelection)에서는 moveBlock이 이동 단위를 찾지 못하므로 블록 화면의 위/아래 버튼이 이것을 쓴다.
      moveBlockAt:
        (pos: number, dir: -1 | 1) =>
        ({ state, tr, dispatch }) => {
          const $pos = state.doc.resolve(pos);
          const parent = $pos.parent;
          const node = state.doc.nodeAt(pos);
          if (!node || !["doc", "column", "blockquote", "bulletList", "orderedList"].includes(parent.type.name)) return false;
          const target = $pos.index() + dir;
          if (target < 0 || target >= parent.childCount) return false;
          if (dispatch) {
            const other = parent.child(target);
            tr.delete(pos, pos + node.nodeSize);
            const at = dir === 1 ? pos + other.nodeSize : pos - other.nodeSize;
            tr.insert(at, node);
            tr.setSelection(NodeSelection.create(tr.doc, at));
            tr.scrollIntoView();
          }
          return true;
        },
    };
  },
});

function textSelectionNear(tr: Transaction, pos: number) {
  return TextSelection.near(tr.doc.resolve(Math.min(pos, tr.doc.content.size)));
}

const ID_TYPES = [
  "paragraph", "heading", "blockquote", "bulletList", "orderedList", "listItem", "horizontalRule", "codeBlock",
  "postImage", "embed", "table", "tableRow", "tableCell", "tableHeader", "columns", "column",
];

export interface SchemaOptions {
  placeholder?: string;
  onReject?: (reason: string) => void;
}

export function blockExtensions({ placeholder = "", onReject }: SchemaOptions = {}): Extensions {
  return [
    Document,
    Paragraph.extend({ group: "block flow" }),
    Text,
    Heading.extend({ group: "block flow" }).configure({ levels: [1, 2, 3, 4, 5, 6] }),
    Blockquote.extend({ group: "block flow", content: "(paragraph | bulletList | orderedList)+" }),
    BulletList.extend({ group: "block flow list" }),
    OrderedList.extend({ group: "block flow list" }),
    ListItem.extend({ content: "paragraph (bulletList | orderedList)?" }),
    ListKeymap,
    ListBackspace,
    CodeBlock.extend({
      group: "block flow",
      addAttributes() {
        return {
          language: {
            default: "plain",
            // 목록 밖 language는 plain으로 둔다(붙여 넣은 HTML 등). 저장 계약도 enum만 받는다.
            parseHTML: (el: HTMLElement) => {
              const cls = [...(el.firstElementChild?.classList ?? [])].find((c) => c.startsWith("language-"));
              const lang = cls?.slice("language-".length);
              return (CODE_LANGUAGES as readonly string[]).includes(lang ?? "") ? lang : "plain";
            },
            renderHTML: (attrs: Record<string, unknown>) => ({ "data-language": attrs.language }),
          },
        };
      },
    }).configure({ defaultLanguage: "plain", enableTabIndentation: true, tabSize: 2 }),
    HorizontalRule.extend({ group: "block flow" }),
    PostHardBreak,
    Bold,
    Italic,
    Underline,
    Strike,
    // 계약은 code와 다른 mark·color·link의 공존을 허용한다(Tiptap 기본은 code가 다른 mark를 모두 배제).
    Code.extend({ excludes: "" }),
    TextStyle,
    TextColor,
    Link.extend({ inclusive: false }).configure({
      autolink: false,
      linkOnPaste: true,
      openOnClick: false,
      protocols: [],
      isAllowedUri: (url) => isAllowedLink(url),
      HTMLAttributes: { rel: "noopener noreferrer nofollow ugc", target: null },
    }),
    TextAlign.configure({ types: ["heading", "paragraph"], alignments: ALIGNS, defaultAlignment: "left" }),
    PostTable.configure({ resizable: false, allowTableNodeSelection: false }),
    TableRow,
    PostTableHeader,
    PostTableCell,
    Columns,
    Column,
    PostImage,
    Embed,
    UniqueID.configure({ types: ID_TYPES, attributeName: "id", generateID: () => uuidV4() }),
    UndoRedo,
    Dropcursor,
    // 표·단·이미지 뒤 빈 자리는 gapcursor로 들어간다(TrailingNode는 열기만 해도 빈 문단을 더해 저장본을 바꾸므로 쓰지 않는다)
    Gapcursor,
    Placeholder.configure({ placeholder }),
    ContractGuard.configure({ onReject: onReject ?? (() => {}) }),
    MoveBlock,
    ImeSafeKeys,
  ];
}

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    textColor: {
      setTextColor: (color: string) => ReturnType;
      unsetTextColor: () => ReturnType;
      setTextSize: (pt: number) => ReturnType;
      unsetTextSize: () => ReturnType;
    };
    columns: {
      insertColumns: (widths: number[]) => ReturnType;
      setColumnWidths: (widths: number[]) => ReturnType;
    };
    postTable: {
      insertTableAfterBlock: (options?: { rows?: number; cols?: number; withHeaderRow?: boolean }) => ReturnType;
    };
    postImage: {
      insertPostImage: (attrs: { imageId: string; alt: string }, at?: number) => ReturnType;
    };
    embed: {
      insertYouTube: (resourceId: string) => ReturnType;
    };
    moveBlock: {
      moveBlock: (dir: -1 | 1) => ReturnType;
      moveBlockAt: (pos: number, dir: -1 | 1) => ReturnType;
    };
  }
}

