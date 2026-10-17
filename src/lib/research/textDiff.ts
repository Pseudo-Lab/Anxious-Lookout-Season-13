// 두 본문의 줄 단위 비교(GitHub 스타일). 서버는 불변 원문만 주고 비교는 브라우저에서 계산한다(#22 api-contracts/issue22-writing-editor-proposal.md).
// 본문은 최대 200,000자라 줄 비교와 단어 강조 전체에 하나의 시간 예산(DIFF_TIMEOUT_MS)을 둔다.
// 줄 비교가 예산 안에 끝나지 않으면 null(비교 불가). 단어 강조는 남은 예산·줄 길이·토큰 수 안에서만 계산하고,
// 넘으면 그 줄부터 강조를 생략한다(줄 단위 결과는 유지). 화면 스레드를 예산 이상 막지 않게 한다.

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

// 줄 안 단어 강조는 짧은 줄에서만 계산한다(글자 수·토큰 수 상한).
const WORD_DIFF_MAX = 1000;
const WORD_TOKENS_MAX = 400;
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

/** 줄 안 단어 강조. 상한을 넘거나 남은 예산 안에 끝나지 않으면 null(강조 없이 줄 단위로만 보인다). */
function wordParts(before: string, after: string, deadline: number): [Part[], Part[]] | null {
  if (before.length > WORD_DIFF_MAX || after.length > WORD_DIFF_MAX) return null;
  const left = deadline - Date.now();
  if (left <= 0) return null;
  const a = tokens(before);
  const b = tokens(after);
  if (a.length > WORD_TOKENS_MAX || b.length > WORD_TOKENS_MAX) return null;
  const changes = diffArrays(a, b, { timeout: left });
  if (!changes) return null;
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
  const deadline = Date.now() + timeout;
  const a = before.replace(/\r\n/g, "\n");
  const b = after.replace(/\r\n/g, "\n");
  const changes = diffLines(a.endsWith("\n") ? a : `${a}\n`, b.endsWith("\n") ? b : `${b}\n`, { timeout });
  if (!changes) return null;
  // 예산을 다 쓰면(줄 비교에서 이미 다 쓴 경우 포함) 이후 줄은 단어 강조를 시도하지 않는다.
  let words = Date.now() < deadline;
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
        let parts: [Part[], Part[]] | null = null;
        if (paired && words) {
          parts = wordParts(text, adds[k], deadline);
          if (Date.now() > deadline) words = false;
        }
        delRows.push({ kind: "del", oldNo: oldNo++, text, parts: parts?.[0] });
        if (paired) addRows.push({ kind: "add", newNo: newNo++, text: adds[k], parts: parts?.[1] });
      });
      removed += dels.length;
      append(rows, delRows);
      if (next?.added) {
        if (!paired) for (const text of adds) addRows.push({ kind: "add", newNo: newNo++, text });
        added += adds.length;
        append(rows, addRows);
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

// 큰 배열을 함수 인자로 펼치면(push(...rows)) 엔진 인자 개수 한도를 넘는다(리뷰 F3). 항상 하나씩 이어 붙인다.
function append<T>(target: T[], items: readonly T[]) {
  for (const item of items) target.push(item);
}

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
      append(current, run.slice(0, head));
      if (current.length) blocks.push({ kind: "rows", rows: current });
      blocks.push({ kind: "fold", id: id++, rows: run.slice(head, run.length - tail) });
      current = run.slice(run.length - tail);
    } else append(current, run);
    i = j;
  }
  if (current.length) blocks.push({ kind: "rows", rows: current });
  return blocks;
}
