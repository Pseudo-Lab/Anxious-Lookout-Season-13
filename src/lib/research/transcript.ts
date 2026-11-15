// #89 대화 화면 상태와 말풍선 연속성.
// - 화면 상태는 한 번에 하나다: ready(입력 가능) → submitted(전송 요청 중) → waiting(서버가 실행 중, "생각 중") → ready | error.
//   streaming은 #90(실시간 스트리밍)에서 waiting 대신 채운다. 레퍼런스: AI SDK useChat status.
// - 보낸 메시지는 세 모습을 거친다: 화면이 먼저 그린 말풍선(local) → 서버 접수 항목(`pending-<requestId>`) →
//   Codex 기록 항목(native id). id가 바뀌어도 같은 말풍선(React key)으로 이어지게 key 별칭(alias)을 둔다.

import type { MessageItem, PageDescriptor, SessionItem, SessionState } from "@/lib/research/types";

export type ChatStatus = "ready" | "submitted" | "waiting" | "streaming" | "error";

/**
 * 화면 상태. 전송 요청 중이 가장 앞선다(서버 상태는 아직 이전 값일 수 있다).
 * streaming(#90): 스트림으로 진행 중인 턴의 항목을 받고 있다. 기록(state)이 아직 이전 값이어도 응답 중으로 본다.
 */
export function chatStatus(sending: boolean, state: SessionState, streaming = false): ChatStatus {
  if (sending) return "submitted";
  if (streaming) return "streaming";
  if (state === "running") return "waiting";
  return state === "failed" ? "error" : "ready";
}

/** 응답을 기다리는 중인지("생각 중" 표시·입력 잠금). */
export const awaitingReply = (status: ChatStatus) => status === "submitted" || status === "waiting" || status === "streaming";

/** 화면이 먼저 그리는 내 메시지. baseline은 보낸 순간 화면에 있던 서버 항목 id다(그 뒤에 생긴 항목만 이 메시지일 수 있다). */
export interface LocalSend {
  key: string;
  text: string;
  page?: PageDescriptor;
  baseline: readonly string[];
}

/** 아직 Codex 기록 항목을 찾지 못한 내 메시지. */
interface Anchor {
  key: string;
  text: string;
  baseline: readonly string[];
}

export interface Continuity {
  /** 서버 항목 id → 화면 key. 한 번 정해지면 이 화면이 있는 동안 유지한다. */
  aliases: Readonly<Record<string, string>>;
  anchor: Anchor | null;
}

export const EMPTY_CONTINUITY: Continuity = { aliases: {}, anchor: null };

export type TranscriptEntry =
  | { kind: "item"; key: string; item: SessionItem }
  | { kind: "local"; key: string; text: string; page?: PageDescriptor };

/** 서버가 접수해 실행 중인 내 입력(아직 Codex 기록에 없음). 정상 전송이므로 경고로 보이지 않는다. */
export const isPendingInput = (item: SessionItem): item is MessageItem =>
  item.type === "message" && item.role === "user" && item.source === "platform" && item.status === "pending";

// 실패로 남은 입력(not_recorded)은 새 메시지가 아니다. 같은 질문을 다시 보낸 경우에도 그 항목에 이어 붙이지 않는다.
const candidate = (item: SessionItem): item is MessageItem =>
  item.type === "message" && item.role === "user" && !(item.source === "platform" && item.status === "not_recorded");

const norm = (text: string) => text.trim();
const own = (o: Readonly<Record<string, string>>, k: string) => (Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined);

/**
 * 서버 항목과 화면이 먼저 그린 메시지를 합쳐 화면 목록과 다음 연속성 상태를 만든다(순수 함수).
 * - local이 있으면 보낸 뒤 새로 생긴 같은 내용의 내 메시지(접수 항목 또는 기록 항목)를 그 말풍선으로 본다.
 *   아직 없으면 local 말풍선을 끝에 붙인다.
 * - 접수 항목만 찾았으면 기록 항목이 생길 때까지 계속 찾는다(대화를 실행 중에 연 경우도 같다).
 */
export function reconcile(
  prev: Continuity,
  items: readonly SessionItem[],
  local: LocalSend | null
): { entries: TranscriptEntry[]; next: Continuity } {
  const aliases: Record<string, string> = { ...prev.aliases };
  let anchor: Anchor | null = local ? { key: local.key, text: norm(local.text), baseline: local.baseline } : prev.anchor;
  let matched = false;
  let recorded = false;
  if (anchor) {
    const baseline = new Set(anchor.baseline);
    for (const item of items) {
      if (!candidate(item)) continue;
      const known = own(aliases, item.id);
      const hit = known === anchor.key || (known === undefined && !baseline.has(item.id) && norm(item.text) === anchor.text);
      if (!hit) continue;
      aliases[item.id] = anchor.key;
      matched = true;
      if (!isPendingInput(item)) recorded = true;
      break;
    }
  }

  const used = new Set<string>();
  const entries: TranscriptEntry[] = items.map((item) => {
    const alias = own(aliases, item.id);
    const key = alias !== undefined && !used.has(alias) ? alias : item.id;
    used.add(key);
    return { kind: "item", key, item };
  });
  if (local && !matched && !used.has(local.key)) entries.push({ kind: "local", key: local.key, text: local.text, page: local.page });

  // 기록 항목을 찾았거나, 보내기가 끝났는데(local 없음) 새 항목이 없으면(이미 처리된 요청의 재전송 등) 더 찾지 않는다.
  if (recorded || (!local && !matched)) anchor = null;
  if (!anchor) {
    // 실행 중에 연 대화: 접수 항목이 기록 항목으로 바뀔 때 같은 말풍선으로 잇는다.
    const pending = items.find(isPendingInput);
    if (pending) {
      anchor = {
        key: own(aliases, pending.id) ?? pending.id,
        text: norm(pending.text),
        baseline: items.filter((i) => i !== pending).map((i) => i.id),
      };
      aliases[pending.id] = anchor.key;
    }
  }
  return { entries, next: { aliases, anchor } };
}
