// #32 읽기 투영과 서식 비교(backend/app/post_blocks.py와 같은 규칙, golden fixture로 고정).
// 저장된 글·초안·제안에는 서버가 계산한 textProjection·formatChanges가 있다. 이 모듈은 아직 저장하지 않은
// 편집 중 내용을 같은 규칙으로 비교할 때 쓴다. 제안 수락 화면은 서버 비교(comparison API)를 기준으로 한다.

import type { BlockDocument } from "./types";

const CHILDREN = ["blocks", "items", "rows", "cells", "columns"] as const;

// 블록·목록 항목·행·칸·단 모두 id를 가진 "노드"다(문서 순서, 깊이 우선)
type AnyNode = Record<string, unknown> & { id: string; type?: string };

const childrenOf = (n: AnyNode, key: (typeof CHILDREN)[number]) => ((n[key] as AnyNode[] | undefined) ?? []);

export function nodes(doc: BlockDocument): AnyNode[] {
  const out: AnyNode[] = [];
  const walk = (n: AnyNode) => {
    out.push(n);
    for (const k of CHILDREN) for (const c of childrenOf(n, k)) walk(c);
  };
  for (const b of doc.blocks) walk(b as unknown as AnyNode);
  return out;
}

const runText = (runs: unknown) => ((runs as { text: string }[] | undefined) ?? []).map((r) => r.text).join("");

function project(n: AnyNode): string {
  switch (n.type) {
    case "paragraph":
    case "heading":
      return runText(n.runs);
    case "divider":
      return "";
    case "code":
      return n.text as string;
    case "image":
    case "embed": {
      const first = n.type === "image" ? (n.alt as string) : `youtube:${n.resourceId as string}`;
      return [first, runText(n.caption)].filter(Boolean).join("\n");
    }
    case "table":
      return childrenOf(n, "rows").map((row) => childrenOf(row, "cells").map(project).join("\t")).join("\n");
    case "list":
      return childrenOf(n, "items").map((item) => childrenOf(item, "blocks").map(project).join("\n")).join("\n");
    case "columns":
      return childrenOf(n, "columns").map(project).join("\n\n");
    default:
      // quote, 목록 항목, 칸, 단
      return childrenOf(n, "blocks").map(project).join("\n\n");
  }
}

/** 계약 plain text projection(trim·정규화 없음). */
export const textProjection = (doc: BlockDocument) => doc.blocks.map((b) => project(b as unknown as AnyNode)).join("\n\n");

/** 본문에 보이는 내용이 있는지(서버 저장 조건: projection이 공백만이 아니거나 이미지·영상이 있음). */
export const hasVisibleContent = (doc: BlockDocument) =>
  textProjection(doc).trim() !== "" || nodes(doc).some((n) => n.type === "image" || n.type === "embed");

/** 구조적 이미지 참조(표지 제외), 정렬·중복 제거. */
export const imageIds = (doc: BlockDocument) => [...new Set(nodes(doc).filter((n) => n.type === "image").map((n) => n.imageId as string))].sort();

export type FormatChangeKind = "format-only" | "layout" | "moved" | "added" | "removed" | "ambiguous";
export interface FormatChange {
  baseBlockId: string | null;
  candidateBlockId: string | null;
  kind: FormatChangeKind;
  changedProperties: string[];
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const sortKeys = (v: unknown): unknown =>
  Array.isArray(v) ? v.map(sortKeys) : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys((v as Record<string, unknown>)[k])])) : v;

function locations(doc: BlockDocument) {
  const out = new Map<string, [string | null, string, number]>();
  const walk = (children: AnyNode[], parent: string | null, field: string) =>
    children.forEach((c, i) => {
      out.set(c.id, [parent, field, i]);
      for (const k of CHILDREN) walk(childrenOf(c, k), c.id, k);
    });
  walk(doc.blocks as unknown as AnyNode[], null, "blocks");
  return out;
}

const local = (n: AnyNode) => Object.fromEntries(Object.entries(n).filter(([k]) => k !== "id" && !(CHILDREN as readonly string[]).includes(k)));

/** base → candidate 서식·배치 변화(같은 id 우선, 나머지는 종류+텍스트 순서 대응). 텍스트가 같아도 서식 변화는 남는다. */
export function formatChanges(base: BlockDocument, candidate: BlockDocument): FormatChange[] {
  const left = nodes(base);
  const right = nodes(candidate);
  const byId = new Map(left.map((n) => [n.id, n]));
  const used = new Set<string>();
  const changes: FormatChange[] = [];
  const keyOf = (n: AnyNode) => JSON.stringify([n.type ?? null, project(n)]);
  const buckets = new Map<string, AnyNode[]>();
  for (const n of left) buckets.set(keyOf(n), [...(buckets.get(keyOf(n)) ?? []), n]);
  const rightIds = new Set(right.map((n) => n.id));
  const reserved = new Set(left.map((n) => n.id).filter((id) => rightIds.has(id)));
  const rightCounts = new Map<string, number>();
  for (const n of right) if (!reserved.has(n.id)) rightCounts.set(keyOf(n), (rightCounts.get(keyOf(n)) ?? 0) + 1);
  const oldLoc = locations(base);
  const newLoc = locations(candidate);

  // 1단계: 후보 노드마다 기준 노드를 정한다(같은 id 우선, 아니면 종류+텍스트 순서 대응)
  const pairs: [AnyNode, AnyNode | undefined, boolean][] = [];
  const matched = new Map<string, string>();
  for (const n of right) {
    let old = !used.has(n.id) ? byId.get(n.id) : undefined;
    let ambiguous = false;
    if (!old) {
      const key = keyOf(n);
      const matches = (buckets.get(key) ?? []).filter((m) => !used.has(m.id) && !reserved.has(m.id));
      if (matches.length) {
        old = matches[0];
        ambiguous = matches.length > 1 || (rightCounts.get(key) ?? 0) > 1;
      }
    }
    pairs.push([n, old, ambiguous]);
    if (old) {
      used.add(old.id);
      matched.set(n.id, old.id);
    }
  }
  // 2단계: 속성·위치 비교. 부모는 대응된 기준 id로 바꿔 비교한다(내용으로 대응된 부모의 id만 바뀐 경우는 이동이 아니다)
  for (const [n, old, ambiguous] of pairs) {
    if (!old) {
      changes.push({ baseBlockId: null, candidateBlockId: n.id, kind: "added", changedProperties: ["structure"] });
      continue;
    }
    const a = local(old);
    const b = local(n);
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((k) => !same(sortKeys(a[k]), sortKeys(b[k]))).sort();
    const [op, of, oi] = oldLoc.get(old.id)!;
    const [np, nf, ni] = newLoc.get(n.id)!;
    const newParent = np === null ? null : (matched.get(np) ?? np);
    const reparented = op !== newParent || of !== nf;
    const moved = oi !== ni;
    if (reparented) keys.push("parent");
    if (moved) keys.push("position");
    if (keys.length || moved || ambiguous) {
      const kind: FormatChangeKind = ambiguous
        ? "ambiguous"
        : reparented || keys.some((k) => ["width", "type", "ordered", "start"].includes(k))
          ? "layout"
          : moved
            ? "moved"
            : "format-only";
      changes.push({ baseBlockId: old.id, candidateBlockId: n.id, kind, changedProperties: keys.length ? keys : ["structure"] });
    }
  }
  for (const n of left) if (!used.has(n.id)) changes.push({ baseBlockId: n.id, candidateBlockId: null, kind: "removed", changedProperties: ["structure"] });
  return changes;
}

// run 서식 순서(marks·link). 글자만 바뀐 run과 서식·링크가 바뀐 run을 구분한다.
const runStyle = (n: AnyNode | undefined) =>
  n ? JSON.stringify(((n.runs as { marks: unknown; link?: string }[] | undefined) ?? []).map((r) => [r.marks, r.link ?? null])) : null;

/**
 * 서식·배치 목록에 보일 변화. 글자 변화는 줄 비교에 보이므로, 같은 블록에서 run의 서식 순서(marks·link)가 그대로이고
 * 글자만 바뀐 경우(changedProperties가 runs뿐)만 뺀다. 링크 대상·서식이 함께 바뀌었으면 글자 변화가 있어도 남긴다.
 */
export function formattingChanges(before: BlockDocument, after: BlockDocument, changes: FormatChange[]): FormatChange[] {
  const base = new Map(nodes(before).map((n) => [n.id, n]));
  const cand = new Map(nodes(after).map((n) => [n.id, n]));
  const textOnly = (c: FormatChange) => {
    if (c.kind !== "format-only" || c.baseBlockId === null || c.candidateBlockId === null) return false;
    if (!c.changedProperties.every((p) => p === "runs")) return false;
    const a = base.get(c.baseBlockId);
    const b = cand.get(c.candidateBlockId);
    return !!a && !!b && runStyle(a) === runStyle(b) && project(a) !== project(b);
  };
  return changes.filter((c) => !textOnly(c));
}
