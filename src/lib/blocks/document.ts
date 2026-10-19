// #32 post-blocks v1 엄격 해석·정규화(api-contracts/issue32-blocks-draft.md v0.3, backend/contracts/post-blocks-v1/).
// 서버 validator와 같은 규칙으로 입력을 검사하고 canonical 문서를 만든다. 첫 오류에서 멈추고 코드·JSON Pointer만 돌려준다
// (입력 원문은 오류에 넣지 않는다). 저장 직전 사전 검사와, 서버가 준 문서를 편집기·renderer에 넘기기 전 검사에 쓴다.
// 서버 판단이 최종이다(이미지 소유·존재는 여기서 확인하지 않는다).

import {
  BLOCKS_FORMAT,
  BLOCKS_VERSION,
  CODE_LANGUAGES,
  IMAGE_WIDTHS,
  SIMPLE_MARKS,
  YOUTUBE_ID,
  type Align,
  type Block,
  type BlockDocument,
  type Column,
  type FlowBlock,
  type ListBlock,
  type ListItem,
  type Mark,
  type ParagraphBlock,
  type Run,
  type TableCell,
  type TableRow,
} from "./types";

export const BLOCK_LIMITS = {
  documentBytes: 1048576,
  textCodepoints: 200000,
  ids: 2000,
  nodeDepth: 8,
  listDepth: 3,
  topLevelBlocks: 500,
  images: 100,
  distinctImagesIncludingCover: 50,
  tables: 20,
  tableRows: 100,
  tableColumns: 20,
  totalCells: 1000,
  columnGroups: 20,
  columnsPerGroup: 3,
  runs: 10000,
  runCodepoints: 20000,
  altCodepoints: 1000,
  captionCodepoints: 2000,
} as const;

export type BlockErrorCode = "invalid_block_document" | "unsupported_schema_version" | "content_limit_exceeded" | "payload_too_large";
/** path는 문서 기준 JSON Pointer(루트 ""). API 오류는 요청 기준이라 앞에 "/content"가 붙는다. */
export interface BlockError {
  status: 413 | 422;
  code: BlockErrorCode;
  path: string;
}
export type ParseResult = { ok: true; doc: BlockDocument } | { ok: false; error: BlockError };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HEX_INPUT = /^#[0-9A-Fa-f]{6}$/;
const ALIGNS: readonly Align[] = ["left", "center", "right"];
// 개행(LF)만 허용. 단독 CR, TAB, 나머지 C0, DEL, C1은 거절한다(CRLF는 먼저 LF로 바꾼다).
const CONTROL = /[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/;
const LINK_CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

/**
 * 계약 링크 정책(서버 safe_link와 같다): http/https(호스트 필수)·mailto(주소 필수) 절대 주소와 #앵커.
 * 공백·제어문자·역슬래시, 계정정보(user:pass@)가 있으면 거절한다.
 */
export function isAllowedLink(url: string): boolean {
  if (!url || /[\s\\]/.test(url) || LINK_CONTROL.test(url)) return false;
  if (url.startsWith("#")) return url.length > 1;
  try {
    const u = new URL(url);
    if (u.username !== "" || u.password !== "") return false;
    if (u.protocol === "http:" || u.protocol === "https:") return u.hostname !== "";
    return u.protocol === "mailto:" && u.pathname !== "" && !url.slice("mailto:".length).startsWith("//");
  } catch {
    return false;
  }
}

const escapeToken = (k: string) => k.replace(/~/g, "~0").replace(/\//g, "~1");
const at = (path: string, key: string | number) => `${path}/${typeof key === "number" ? key : escapeToken(key)}`;
const codepoints = (s: string) => {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    // 서로게이트 쌍은 code point 하나다
    if (c < 0xd800 || c > 0xdbff) n++;
  }
  return n;
};

class Fail extends Error {
  constructor(readonly error: BlockError) {
    super(error.code);
  }
}
const shape = (path: string): never => {
  throw new Fail({ status: 422, code: "invalid_block_document", path });
};
const limit = (path: string): never => {
  throw new Fail({ status: 422, code: "content_limit_exceeded", path });
};

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

function object(v: unknown, path: string, allowed: readonly string[]): Obj {
  if (!isObj(v)) shape(path);
  const o = v as Obj;
  for (const k of Object.keys(o)) if (!allowed.includes(k)) shape(at(path, k));
  return o;
}
function array(v: unknown, path: string, min = 0, max = Infinity): unknown[] {
  if (!Array.isArray(v) || v.length < min || v.length > max) shape(path);
  return v as unknown[];
}
function str(v: unknown, path: string): string {
  if (typeof v !== "string") shape(path);
  return v as string;
}
function oneOf<T extends string | number>(v: unknown, path: string, allowed: readonly T[]): T {
  if (!allowed.includes(v as T) || typeof v === "boolean") shape(path);
  return v as T;
}
function optional<T>(o: Obj, key: string, fallback: T, read: (v: unknown, path: string) => T, path: string): T {
  return o[key] === undefined ? fallback : read(o[key], at(path, key));
}

const SIMPLE_ORDER = SIMPLE_MARKS as readonly string[];

class Parser {
  private ids = new Set<string>();
  private counts = { text: 0, runs: 0, images: 0, imageIds: new Set<string>(), tables: 0, cells: 0, columnGroups: 0 };

  /** 개행 정규화 후 제어문자 검사. allowLf=false면 개행도 거절(alt). */
  private text(v: unknown, path: string, { allowLf = true, min = 0 } = {}): string {
    const s = str(v, path).replace(/\r\n/g, "\n");
    if (codepoints(s) < min) shape(path);
    if (CONTROL.test(s) || (!allowLf && s.includes("\n"))) shape(path);
    return s;
  }

  private id(o: Obj, path: string, depth: number): string {
    const p = at(path, "id");
    const id = str(o.id, p);
    if (!UUID.test(id)) shape(p);
    if (this.ids.has(id)) shape(p);
    this.ids.add(id);
    if (this.ids.size > BLOCK_LIMITS.ids) limit(p);
    if (depth > BLOCK_LIMITS.nodeDepth) limit(path);
    return id;
  }

  private marks(v: unknown, path: string): Mark[] {
    const list = array(v, path);
    const seen = new Set<string>();
    const out: Mark[] = [];
    list.forEach((m, i) => {
      const p = at(path, i);
      let key: string;
      if (typeof m === "string") {
        if (!SIMPLE_ORDER.includes(m)) shape(p);
        key = m;
        out.push(m as Mark);
      } else {
        const o = object(m, p, ["color"]);
        const c = str(o.color, at(p, "color"));
        if (!HEX_INPUT.test(c)) shape(at(p, "color"));
        key = "color";
        out.push({ color: c.toUpperCase() });
      }
      if (seen.has(key)) shape(p);
      seen.add(key);
    });
    const rank = (m: Mark) => (typeof m === "string" ? SIMPLE_ORDER.indexOf(m) : SIMPLE_ORDER.length);
    return out.sort((a, b) => rank(a) - rank(b));
  }

  /** runs: 정규화 → 같은 marks/link 인접 run 병합 → 개수·길이 상한. caption이면 link 금지, 합계 상한. */
  private runs(v: unknown, path: string, caption = false): Run[] {
    const list = array(v, path);
    const parsed: Run[] = list.map((r, i) => {
      const p = at(path, i);
      const o = object(r, p, caption ? ["text", "marks"] : ["text", "marks", "link"]);
      const text = this.text(o.text, at(p, "text"), { min: 1 });
      const marks = optional(o, "marks", [] as Mark[], (x, q) => this.marks(x, q), p);
      const run: Run = { text, marks };
      if (o.link !== undefined && o.link !== null) {
        const link = str(o.link, at(p, "link"));
        if (!isAllowedLink(link)) shape(at(p, "link"));
        run.link = link;
      }
      return run;
    });
    const merged: Run[] = [];
    for (const r of parsed) {
      const prev = merged[merged.length - 1];
      if (prev && prev.link === r.link && JSON.stringify(prev.marks) === JSON.stringify(r.marks)) prev.text += r.text;
      else merged.push({ ...r, marks: [...r.marks] });
    }
    let total = 0;
    for (const r of merged) {
      const n = codepoints(r.text);
      if (n > BLOCK_LIMITS.runCodepoints) limit(path);
      total += n;
    }
    if (caption && total > BLOCK_LIMITS.captionCodepoints) limit(path);
    this.counts.runs += merged.length;
    this.counts.text += total;
    if (this.counts.runs > BLOCK_LIMITS.runs || this.counts.text > BLOCK_LIMITS.textCodepoints) limit(path);
    return merged;
  }

  private align(o: Obj, path: string): Align {
    return optional(o, "align", "left" as Align, (v, p) => oneOf(v, p, ALIGNS), path);
  }

  private paragraph(o: Obj, path: string, depth: number, inCell = false): ParagraphBlock {
    object(o, path, ["id", "type", "runs", "align"]);
    const id = this.id(o, path, depth);
    const runs = this.runs(o.runs, at(path, "runs"));
    if (inCell) {
      // 셀 안 paragraph는 정렬을 갖지 않는다(cell.align만 쓴다). 입력은 생략 또는 left만, canonical은 생략.
      if (o.align !== undefined && o.align !== "left") shape(at(path, "align"));
      return { id, type: "paragraph", runs };
    }
    return { id, type: "paragraph", runs, align: this.align(o, path) };
  }

  private list(o: Obj, path: string, depth: number, listDepth: number): ListBlock {
    object(o, path, ["id", "type", "ordered", "start", "items"]);
    const id = this.id(o, path, depth);
    if (typeof o.ordered !== "boolean") shape(at(path, "ordered"));
    const ordered = o.ordered as boolean;
    let start: number | undefined;
    if (o.start !== undefined && o.start !== null) {
      if (!isInt(o.start) || o.start < 1 || o.start > 9999) shape(at(path, "start"));
      if (!ordered) shape(at(path, "start"));
      start = o.start as number;
    } else if (ordered) start = 1;
    const items: ListItem[] = array(o.items, at(path, "items"), 1).map((it, i) => {
      const p = at(at(path, "items"), i);
      const io = object(it, p, ["id", "blocks"]);
      const itemId = this.id(io, p, depth + 1);
      const bp = at(p, "blocks");
      const bl = array(io.blocks, bp, 1, 2);
      const first = this.typed(bl[0], at(bp, 0), ["paragraph"]);
      const para = this.paragraph(first, at(bp, 0), depth + 2);
      if (bl.length === 1) return { id: itemId, blocks: [para] };
      const second = this.typed(bl[1], at(bp, 1), ["list"]);
      if (listDepth + 1 > BLOCK_LIMITS.listDepth) limit(at(bp, 1));
      return { id: itemId, blocks: [para, this.list(second, at(bp, 1), depth + 2, listDepth + 1)] };
    });
    const out: ListBlock = { id, type: "list", ordered, items };
    if (start !== undefined) out.start = start;
    return out;
  }

  /** 블록 object와 type이 이 자리에서 허용되는지. */
  private typed(v: unknown, path: string, allowed: readonly string[]): Obj {
    if (!isObj(v)) shape(path);
    const o = v as Obj;
    if (typeof o.type !== "string" || !allowed.includes(o.type)) shape(at(path, "type"));
    return o;
  }

  private block(v: unknown, path: string, depth: number, allowed: readonly string[]): Block {
    const o = this.typed(v, path, allowed);
    switch (o.type) {
      case "paragraph":
        return this.paragraph(o, path, depth);
      case "heading": {
        object(o, path, ["id", "type", "level", "runs", "align"]);
        const id = this.id(o, path, depth);
        const level = oneOf(o.level, at(path, "level"), [1, 2, 3, 4, 5, 6] as const);
        return { id, type: "heading", level, runs: this.runs(o.runs, at(path, "runs")), align: this.align(o, path) };
      }
      case "quote": {
        object(o, path, ["id", "type", "blocks"]);
        const id = this.id(o, path, depth);
        const blocks = array(o.blocks, at(path, "blocks"), 1).map((b, i) => this.block(b, at(at(path, "blocks"), i), depth + 1, ["paragraph", "list"]));
        return { id, type: "quote", blocks: blocks as (ParagraphBlock | ListBlock)[] };
      }
      case "list":
        return this.list(o, path, depth, 1);
      case "divider":
        object(o, path, ["id", "type"]);
        return { id: this.id(o, path, depth), type: "divider" };
      case "code": {
        object(o, path, ["id", "type", "text", "language"]);
        const id = this.id(o, path, depth);
        const text = this.text(o.text, at(path, "text"));
        const language = optional(o, "language", "plain" as const, (x, p) => oneOf(x, p, CODE_LANGUAGES), path);
        this.counts.text += codepoints(text);
        if (this.counts.text > BLOCK_LIMITS.textCodepoints) limit(path);
        return { id, type: "code", text, language };
      }
      case "image": {
        object(o, path, ["id", "type", "imageId", "alt", "caption", "widthPercent", "align"]);
        const id = this.id(o, path, depth);
        const imageId = str(o.imageId, at(path, "imageId"));
        if (!UUID.test(imageId)) shape(at(path, "imageId"));
        const alt = optional(o, "alt", "", (x, p) => this.text(x, p, { allowLf: false }), path);
        if (codepoints(alt) > BLOCK_LIMITS.altCodepoints) limit(at(path, "alt"));
        this.counts.text += codepoints(alt);
        const caption = optional(o, "caption", [] as Run[], (x, p) => this.runs(x, p, true), path);
        const widthPercent = optional(o, "widthPercent", 100 as const, (x, p) => oneOf(x, p, IMAGE_WIDTHS), path);
        const align = this.align(o, path);
        this.counts.images++;
        this.counts.imageIds.add(imageId);
        if (this.counts.images > BLOCK_LIMITS.images || this.counts.imageIds.size > BLOCK_LIMITS.distinctImagesIncludingCover || this.counts.text > BLOCK_LIMITS.textCodepoints) limit(path);
        return { id, type: "image", imageId, alt, caption, widthPercent, align };
      }
      case "table":
        return this.table(o, path, depth);
      case "columns": {
        object(o, path, ["id", "type", "columns"]);
        const id = this.id(o, path, depth);
        if (++this.counts.columnGroups > BLOCK_LIMITS.columnGroups) limit(path);
        const columns: Column[] = array(o.columns, at(path, "columns"), 2, BLOCK_LIMITS.columnsPerGroup).map((c, i) => {
          const p = at(at(path, "columns"), i);
          const co = object(c, p, ["id", "width", "blocks"]);
          const cid = this.id(co, p, depth + 1);
          const width = oneOf(co.width, at(p, "width"), [1, 2, 3, 4] as const);
          const blocks = array(co.blocks, at(p, "blocks"), 1).map((b, j) => this.block(b, at(at(p, "blocks"), j), depth + 2, FLOW_TYPES) as FlowBlock);
          return { id: cid, width, blocks };
        });
        return { id, type: "columns", columns };
      }
      case "embed": {
        object(o, path, ["id", "type", "provider", "resourceId", "caption"]);
        const id = this.id(o, path, depth);
        const provider = oneOf(o.provider, at(path, "provider"), ["youtube"] as const);
        const resourceId = str(o.resourceId, at(path, "resourceId"));
        if (!YOUTUBE_ID.test(resourceId)) shape(at(path, "resourceId"));
        const caption = optional(o, "caption", [] as Run[], (x, p) => this.runs(x, p, true), path);
        return { id, type: "embed", provider, resourceId, caption };
      }
      default:
        return shape(at(path, "type"));
    }
  }

  private table(o: Obj, path: string, depth: number): Block {
    object(o, path, ["id", "type", "rows"]);
    const id = this.id(o, path, depth);
    if (++this.counts.tables > BLOCK_LIMITS.tables) limit(path);
    const rp = at(path, "rows");
    const rowsIn = array(o.rows, rp, 1);
    if (rowsIn.length > BLOCK_LIMITS.tableRows) limit(rp);
    let width = -1;
    const rows: TableRow[] = rowsIn.map((r, i) => {
      const p = at(rp, i);
      const ro = object(r, p, ["id", "header", "cells"]);
      const rid = this.id(ro, p, depth + 1);
      const header = optional(ro, "header", false, (x, q) => (typeof x === "boolean" ? x : shape(q)), p);
      const cp = at(p, "cells");
      const cellsIn = array(ro.cells, cp, 1);
      if (cellsIn.length > BLOCK_LIMITS.tableColumns) limit(cp);
      const cells: TableCell[] = cellsIn.map((c, j) => {
        const q = at(cp, j);
        const co = object(c, q, ["id", "blocks", "align", "background"]);
        const cid = this.id(co, q, depth + 2);
        const blocks = array(co.blocks, at(q, "blocks"), 1).map((b, k) => {
          const bq = at(at(q, "blocks"), k);
          return this.paragraph(this.typed(b, bq, ["paragraph"]), bq, depth + 3, true);
        });
        const cell: TableCell = { id: cid, blocks, align: this.align(co, q) };
        if (co.background !== undefined && co.background !== null) {
          const bg = str(co.background, at(q, "background"));
          if (!HEX_INPUT.test(bg)) shape(at(q, "background"));
          cell.background = bg.toUpperCase();
        }
        if (++this.counts.cells > BLOCK_LIMITS.totalCells) limit(q);
        return cell;
      });
      // 의미 검사: 머리글은 첫 행만, 모든 행 열 수 동일
      if (header && i > 0) shape(at(p, "header"));
      if (width !== -1 && cells.length !== width) shape(cp);
      width = cells.length;
      return { id: rid, header, cells };
    });
    return { id, type: "table", rows };
  }

  doc(v: unknown): BlockDocument {
    const o = object(v, "", ["format", "version", "blocks"]);
    if (o.format !== BLOCKS_FORMAT) shape("/format");
    if (!isInt(o.version)) shape("/version");
    if (o.version !== BLOCKS_VERSION) throw new Fail({ status: 422, code: "unsupported_schema_version", path: "/version" });
    const list = array(o.blocks, "/blocks", 1);
    if (list.length > BLOCK_LIMITS.topLevelBlocks) limit("/blocks");
    const blocks = list.map((b, i) => this.block(b, at("/blocks", i), 1, TOP_TYPES));
    return { format: BLOCKS_FORMAT, version: BLOCKS_VERSION, blocks };
  }
}

const FLOW_TYPES = ["paragraph", "heading", "quote", "list", "divider", "code", "image", "embed"] as const;
const TOP_TYPES = [...FLOW_TYPES, "table", "columns"] as const;

/** 키를 정렬한 공백 없는 JSON(계약 §1 hash·크기 기준). */
export function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (isObj(v)) {
    return `{${Object.keys(v)
      .filter((k) => v[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(v[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(v);
}

export const canonicalBytes = (doc: BlockDocument) => new TextEncoder().encode(canonicalJson(doc)).length;

// 짝 없는 UTF-16 서로게이트(UTF-8로 인코딩할 수 없는 문자열)
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;

/**
 * 구조 검사 전 원시 입력 검사(서버와 같은 순서): 중첩 40단·값 100000개 초과는 content_limit_exceeded,
 * 인코딩할 수 없는 문자열(키 포함)은 invalid_block_document, 원시 UTF-8 JSON 1 MiB 초과는 413. 모두 루트 경로다.
 */
function rawProblem(raw: unknown): BlockError | null {
  const pending: [unknown, number][] = [[raw, 0]];
  let values = 0;
  let malformed = false;
  while (pending.length) {
    const [v, depth] = pending.pop()!;
    if (depth > 40 || ++values > 100000) return { status: 422, code: "content_limit_exceeded", path: "" };
    if (typeof v === "string") malformed ||= LONE_SURROGATE.test(v);
    else if (Array.isArray(v)) for (const x of v) pending.push([x, depth + 1]);
    else if (isObj(v))
      for (const [k, x] of Object.entries(v)) {
        malformed ||= LONE_SURROGATE.test(k);
        pending.push([x, depth + 1]);
      }
  }
  if (malformed) return { status: 422, code: "invalid_block_document", path: "" };
  if (new TextEncoder().encode(canonicalJson(raw)).length > BLOCK_LIMITS.documentBytes) return { status: 413, code: "payload_too_large", path: "" };
  return null;
}

/** 입력을 검사하고 canonical 문서를 만든다. 실패하면 첫 오류의 코드와 문서 기준 path. */
export function parseBlockDocument(input: unknown): ParseResult {
  const raw = rawProblem(input);
  if (raw) return { ok: false, error: raw };
  try {
    const doc = new Parser().doc(input);
    if (canonicalBytes(doc) > BLOCK_LIMITS.documentBytes) return { ok: false, error: { status: 413, code: "payload_too_large", path: "" } };
    return { ok: true, doc };
  } catch (e) {
    if (e instanceof Fail) return { ok: false, error: e.error };
    throw e;
  }
}
