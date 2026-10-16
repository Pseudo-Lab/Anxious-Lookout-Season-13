// 두 본문의 줄 단위 비교(GitHub 스타일). 서버는 불변 원문만 주고 비교는 브라우저에서 계산한다(#22 api-contracts/issue22-writing-editor-proposal.md).
// 본문은 최대 200,000자라 계산 시간에 상한을 두고, 넘으면 비교 결과 대신 null을 돌려준다(화면이 멈추지 않게).

import { diffArrays, diffLines } from "diff";

export interface Part {
  text: string;
  changed: boolean;
}

export type Row =
  | { kind: "context"; oldNo: number; newNo: number; text: string }
  | { kind: "del"; oldNo: number; text: string; parts?: Part[] }
  | { kind: "add"; newNo: number; text: string; parts?: Part[] };

export interface LineDiff {
  rows: Row[];
  added: number;
  removed: number;
}

// 줄 안 단어 강조는 짧은 줄에서만 계산한다.
const WORD_DIFF_MAX = 2000;
export const DIFF_TIMEOUT_MS = 1500;

const splitLines = (value: string) => {
  const lines = value.split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines;
};

// 단어 단위로 자른다. 한글은 공백 없이 글자마다 끊기지 않게 Intl.Segmenter(단어)를 쓰고, 없으면 공백 기준으로 자른다.
const segmenter = typeof Intl !== "undefined" && "Segmenter" in Intl ? new Intl.Segmenter("ko", { granularity: "word" }) : null;
const tokens = (value: string) =>
  segmenter ? Array.from(segmenter.segment(value), (s) => s.segment) : value.split(/(\s+)/).filter(Boolean);

function wordParts(before: string, after: string): [Part[], Part[]] | null {
  if (before.length > WORD_DIFF_MAX || after.length > WORD_DIFF_MAX) return null;
  const changes = diffArrays(tokens(before), tokens(after));
  const del: Part[] = [];
  const add: Part[] = [];
  for (const c of changes) {
    const text = c.value.join("");
    if (!c.added) del.push({ text, changed: !!c.removed });
    if (!c.removed) add.push({ text, changed: !!c.added });
  }
  return [del, add];
}

/** 줄 단위 비교. 시간 상한을 넘으면 null. 줄 끝 개행 차이는 무시하고 \r\n은 \n으로 본다. */
export function lineDiff(before: string, after: string, timeout = DIFF_TIMEOUT_MS): LineDiff | null {
  const a = before.replace(/\r\n/g, "\n");
  const b = after.replace(/\r\n/g, "\n");
  const changes = diffLines(a.endsWith("\n") ? a : `${a}\n`, b.endsWith("\n") ? b : `${b}\n`, { timeout });
  if (!changes) return null;
  const rows: Row[] = [];
  let oldNo = 1;
  let newNo = 1;
  let added = 0;
  let removed = 0;
  for (let i = 0; i < changes.length; i++) {
    const c = changes[i];
    if (c.removed) {
      const dels = splitLines(c.value);
      // 바로 뒤의 추가 덩어리와 줄 수가 같으면 줄끼리 짝지어 바뀐 단어를 강조한다.
      const next = changes[i + 1];
      const adds = next?.added ? splitLines(next.value) : [];
      const paired = adds.length === dels.length;
      const delRows: Row[] = [];
      const addRows: Row[] = [];
      dels.forEach((text, k) => {
        const parts = paired ? wordParts(text, adds[k]) : null;
        delRows.push({ kind: "del", oldNo: oldNo++, text, parts: parts?.[0] });
        if (paired) addRows.push({ kind: "add", newNo: newNo++, text: adds[k], parts: parts?.[1] });
      });
      removed += dels.length;
      rows.push(...delRows);
      if (next?.added) {
        if (!paired) for (const text of adds) addRows.push({ kind: "add", newNo: newNo++, text });
        added += adds.length;
        rows.push(...addRows);
        i++;
      }
    } else if (c.added) {
      for (const text of splitLines(c.value)) rows.push({ kind: "add", newNo: newNo++, text });
      added += splitLines(c.value).length;
    } else {
      for (const text of splitLines(c.value)) rows.push({ kind: "context", oldNo: oldNo++, newNo: newNo++, text });
    }
  }
  return { rows, added, removed };
}

export type Block = { kind: "rows"; rows: Row[] } | { kind: "fold"; id: number; rows: Row[] };

/** 바뀐 줄 주변 context줄만 남기고 나머지 같은 줄은 접는다. */
export function foldContext(rows: Row[], context = 3): Block[] {
  const blocks: Block[] = [];
  let current: Row[] = [];
  let i = 0;
  let id = 0;
  while (i < rows.length) {
    if (rows[i].kind !== "context") {
      current.push(rows[i++]);
      continue;
    }
    let j = i;
    while (j < rows.length && rows[j].kind === "context") j++;
    const run = rows.slice(i, j);
    const head = i === 0 ? 0 : context;
    const tail = j === rows.length ? 0 : context;
    if (run.length > head + tail + 1) {
      current.push(...run.slice(0, head));
      if (current.length) blocks.push({ kind: "rows", rows: current });
      blocks.push({ kind: "fold", id: id++, rows: run.slice(head, run.length - tail) });
      current = run.slice(run.length - tail);
    } else current.push(...run);
    i = j;
  }
  if (current.length) blocks.push({ kind: "rows", rows: current });
  return blocks;
}
