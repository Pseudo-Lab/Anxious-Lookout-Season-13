// #32 모의 API용 post-blocks 도우미. 실제 backend 변환기·validator가 아니다(계약 형태만 흉내 낸다).
// - fromMarkdown: 이관 seed와 모의 Codex 제안용 아주 작은 Markdown → 블록 변환(제목·코드·표·문단, 인라인 기호는 글자 그대로).
// - projection/imageIds: 계약 README의 plain projection·구조적 이미지 참조.
// - changes: 같은 id끼리 속성 비교만 하는 단순 formatChanges(모의 comparison용).
import crypto from "node:crypto";

const uuid = () => crypto.randomUUID();
const para = (text) => ({ id: uuid(), type: "paragraph", runs: text ? [{ text, marks: [] }] : [], align: "left" });
export const doc = (blocks) => ({ format: "post-blocks", version: 1, blocks: blocks.length ? blocks : [para("")] });
export const paragraph = para;

export function fromMarkdown(md) {
  const blocks = [];
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push({ id: uuid(), type: "code", text: `${body.join("\n")}\n`, language: fence[1] || "plain" });
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ id: uuid(), type: "heading", level: h[1].length, runs: [{ text: h[2], marks: [] }], align: "left" });
      i++;
      continue;
    }
    if (line.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        if (!/^\|[\s:|-]+\|$/.test(lines[i])) rows.push(lines[i].slice(1, -1).split("|").map((c) => c.trim()));
        i++;
      }
      blocks.push({
        id: uuid(),
        type: "table",
        rows: rows.map((cells, r) => ({
          id: uuid(),
          header: r === 0,
          cells: cells.map((c) => ({ id: uuid(), align: "left", blocks: [{ id: uuid(), type: "paragraph", runs: c ? [{ text: c, marks: [] }] : [] }] })),
        })),
      });
      continue;
    }
    const text = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\|)/.test(lines[i])) text.push(lines[i++]);
    blocks.push(para(text.join("\n")));
  }
  return doc(blocks);
}

/** 형식만 확인한다(모의). 실제 서버는 전체 계약 검사·정규화를 한다. */
export const isDoc = (v) => !!v && typeof v === "object" && v.format === "post-blocks" && v.version === 1 && Array.isArray(v.blocks) && v.blocks.length > 0;

const CHILDREN = ["blocks", "items", "rows", "cells", "columns"];
export function nodes(d) {
  const out = [];
  const walk = (n) => {
    out.push(n);
    for (const k of CHILDREN) for (const c of n[k] ?? []) walk(c);
  };
  for (const b of d.blocks) walk(b);
  return out;
}
const runText = (runs) => (runs ?? []).map((r) => r.text).join("");
function project(n) {
  switch (n.type) {
    case "paragraph":
    case "heading":
      return runText(n.runs);
    case "divider":
      return "";
    case "code":
      return n.text;
    case "image":
    case "embed":
      return [n.type === "image" ? n.alt : `youtube:${n.resourceId}`, runText(n.caption)].filter(Boolean).join("\n");
    case "table":
      return n.rows.map((row) => row.cells.map(project).join("\t")).join("\n");
    case "list":
      return n.items.map((item) => item.blocks.map(project).join("\n")).join("\n");
    case "columns":
      return n.columns.map(project).join("\n\n");
    default:
      return (n.blocks ?? []).map(project).join("\n\n");
  }
}
export const projection = (d) => d.blocks.map(project).join("\n\n");
export const imageIds = (d) => [...new Set(nodes(d).filter((n) => n.type === "image").map((n) => n.imageId))];
export const visible = (d) => projection(d).trim() !== "" || nodes(d).some((n) => n.type === "image" || n.type === "embed");

const local = (n) => JSON.stringify(Object.fromEntries(Object.entries(n).filter(([k]) => k !== "id" && !CHILDREN.includes(k)).sort()));
export function changes(base, cand) {
  const left = new Map(nodes(base).map((n) => [n.id, n]));
  const out = [];
  const seen = new Set();
  for (const n of nodes(cand)) {
    const o = left.get(n.id);
    if (!o) {
      out.push({ baseBlockId: null, candidateBlockId: n.id, kind: "added", changedProperties: ["structure"] });
      continue;
    }
    seen.add(n.id);
    if (local(o) !== local(n)) {
      const keys = [...new Set([...Object.keys(o), ...Object.keys(n)])].filter((k) => k !== "id" && !CHILDREN.includes(k) && JSON.stringify(o[k]) !== JSON.stringify(n[k])).sort();
      out.push({ baseBlockId: o.id, candidateBlockId: n.id, kind: "format-only", changedProperties: keys });
    }
  }
  for (const id of left.keys()) if (!seen.has(id)) out.push({ baseBlockId: id, candidateBlockId: null, kind: "removed", changedProperties: ["structure"] });
  return out;
}
