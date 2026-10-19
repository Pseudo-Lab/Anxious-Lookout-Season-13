import { Extension, Node, mergeAttributes, type Extensions } from "@tiptap/core";
import { Plugin, PluginKey, TextSelection, type Transaction } from "@tiptap/pm/state";
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
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import UniqueID from "@tiptap/extension-unique-id";
import { uuidV4 } from "@/lib/research/idempotency";
import { CODE_LANGUAGES, IMAGE_WIDTHS, LIMITS, YOUTUBE_ID, type Align } from "@/lib/blocks/types";
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

// ---- 텍스트 색(textStyle.color). 계약 hex만 저장한다. ----
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
    widthPercent: intAttr("width-percent", IMAGE_WIDTHS, 100),
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
    HardBreak,
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
    Table.configure({ resizable: false, allowTableNodeSelection: false }),
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
    };
    columns: {
      insertColumns: (widths: number[]) => ReturnType;
      setColumnWidths: (widths: number[]) => ReturnType;
    };
    postImage: {
      insertPostImage: (attrs: { imageId: string; alt: string }, at?: number) => ReturnType;
    };
    embed: {
      insertYouTube: (resourceId: string) => ReturnType;
    };
    moveBlock: {
      moveBlock: (dir: -1 | 1) => ReturnType;
    };
  }
}

