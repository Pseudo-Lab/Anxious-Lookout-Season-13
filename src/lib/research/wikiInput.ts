// #105 입력 확인(한 줄 요약, 관계 근거 위치). 길이는 모두 Unicode code point 기준이다(계약 1절).
// 브라우저 maxLength는 UTF-16 단위라 이모지 등에서 일찍 잘리므로 쓰지 않고 여기서 확인한다.

import type { EvidenceInput } from "@/lib/research/api";
import { cpLength } from "@/lib/research/bibliography";
import { LIMITS, type Evidence } from "@/lib/research/types";

/** 한 줄 요약 확인. 빈 값은 null(요약 없음). */
export function readSummary(text: string): { ok: true; value: string | null } | { ok: false; error: string } {
  const value = text.trim();
  if (!value) return { ok: true, value: null };
  if (/[\r\n]/.test(value)) return { ok: false, error: "한 줄 요약에는 줄바꿈을 넣을 수 없습니다." };
  if (cpLength(value) > LIMITS.summary) return { ok: false, error: `한 줄 요약은 ${LIMITS.summary}자 이하로 입력해 주세요.` };
  return { ok: true, value };
}

/** 근거 위치 편집 행. key는 화면 목록용. */
export interface EvidenceDraft {
  key: number;
  itemId: string;
  versionId: string;
  // 이미 저장된 근거의 버전 번호(첫 페이지 밖의 옛 버전도 선택지에 보이게)
  versionNumber?: number;
  locator: string;
  quote: string;
}

let draftSeq = 0;
export const nextDraftKey = () => ++draftSeq;

export const toDraft = (e: Evidence): EvidenceDraft => ({
  key: nextDraftKey(),
  itemId: e.itemId,
  versionId: e.versionId,
  versionNumber: e.versionNumber,
  locator: e.locator ?? "",
  quote: e.quote ?? "",
});

export const evidencePayload = (rows: EvidenceDraft[]): EvidenceInput[] =>
  rows.map((r) => {
    const locator = r.locator.trim();
    const quote = r.quote.trim();
    return { itemId: r.itemId, versionId: r.versionId, locator: locator || null, quote: quote || null };
  });

export const savedPayload = (list: Evidence[]): EvidenceInput[] =>
  list.map((e) => ({ itemId: e.itemId, versionId: e.versionId, locator: e.locator, quote: e.quote }));

/** 저장 전 확인. 문제가 없으면 null. */
export function checkEvidence(rows: EvidenceDraft[]): string | null {
  if (rows.length > LIMITS.evidence) return `근거 위치는 ${LIMITS.evidence}개까지 넣을 수 있습니다.`;
  for (const [i, r] of rows.entries()) {
    const n = i + 1;
    if (!r.itemId || !r.versionId) return `근거 위치 ${n}: 항목과 버전을 골라 주세요.`;
    if (!r.locator.trim() && !r.quote.trim()) return `근거 위치 ${n}: 위치(쪽·절)나 인용문 중 하나는 입력해 주세요.`;
    if (cpLength(r.locator.trim()) > LIMITS.locator) return `근거 위치 ${n}: 위치는 ${LIMITS.locator}자 이하로 입력해 주세요.`;
    if (cpLength(r.quote.trim()) > LIMITS.quote) return `근거 위치 ${n}: 인용문은 ${LIMITS.quote}자 이하로 입력해 주세요.`;
  }
  const seen = new Set(evidencePayload(rows).map((e) => JSON.stringify(e)));
  if (seen.size !== rows.length) return "같은 근거 위치가 두 번 들어 있습니다. 하나를 지워 주세요.";
  return null;
}
