import { apiUrl, fetchJson, type FetchFailure } from "@/lib/api/client";

// api-contracts/issue97-pat-r3-v1.md. 관리 요청은 cookie/CSRF만 사용한다. 비밀 값은 목록 타입에 없다.
export type PatScope = "read" | "write";
export interface Pat {
  id: string;
  name: string;
  scope: PatScope;
  issuedAt: string;
  expiresAt: string;
  revokedAt: string | null;
  lastUsedAt: string | null;
  effectiveScope: "public" | "read" | "write" | "none";
  scopeLimitedByRole: boolean;
}
export interface PatInput { name: string; scope: PatScope; expiryDays: number }
export interface IssuedPat { credential: Pat; token: string | null; secretAvailable: boolean; replayed: boolean }
type Obj = Record<string, unknown>;
const obj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const time = (v: unknown): v is string => typeof v === "string" && Number.isFinite(Date.parse(v));

export function parsePat(v: unknown): Pat | null {
  if (!obj(v) || !uuid(v.id) || typeof v.name !== "string" || !v.name.trim() || v.name.length > 80 || /[\u0000-\u001f\u007f]/.test(v.name) || (v.scope !== "read" && v.scope !== "write") || !time(v.issuedAt) || !time(v.expiresAt) || (v.revokedAt !== null && !time(v.revokedAt)) || (v.lastUsedAt !== null && !time(v.lastUsedAt)) || !["public", "read", "write", "none"].includes(v.effectiveScope as string) || typeof v.scopeLimitedByRole !== "boolean") return null;
  return {
    id: v.id, name: v.name, scope: v.scope, issuedAt: v.issuedAt, expiresAt: v.expiresAt,
    revokedAt: v.revokedAt, lastUsedAt: v.lastUsedAt, effectiveScope: v.effectiveScope as Pat["effectiveScope"], scopeLimitedByRole: v.scopeLimitedByRole,
  };
}
export function parsePatPage(v: unknown): { items: Pat[]; nextCursor: string | null } | null {
  if (!obj(v) || !Array.isArray(v.items) || (v.nextCursor !== null && typeof v.nextCursor !== "string")) return null;
  const items = v.items.map(parsePat);
  return items.some((item) => item === null) ? null : { items: items as Pat[], nextCursor: v.nextCursor };
}
export function parseIssuedPat(v: unknown): IssuedPat | null {
  if (!obj(v)) return null;
  const credential = parsePat(v.credential);
  if (!credential) return null;
  if (v.token === null && v.secretAvailable === false && v.replayed === true) return { credential, token: null, secretAvailable: false, replayed: true };
  if (typeof v.token !== "string" || v.secretAvailable !== true || v.replayed !== false) return null;
  const match = /^rpat1\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([A-Za-z0-9_-]{43})$/i.exec(v.token);
  if (!match || match[1].toLowerCase() !== credential.id.toLowerCase()) return null;
  return { credential, token: v.token, secretAvailable: true, replayed: false };
}
const parseRevoke = (v: unknown): { credential: Pat } | null => {
  if (!obj(v)) return null;
  const credential = parsePat(v.credential);
  return credential && credential.revokedAt !== null ? { credential } : null;
};

export const listPats = (cursor: string | null) => {
  const params = new URLSearchParams({ limit: "30" });
  if (cursor) params.set("cursor", cursor);
  return fetchJson(apiUrl(`/research/pats?${params}`), parsePatPage, { credentials: "include" });
};
export const issuePat = async (body: PatInput, csrfToken: string, key: string) => {
  const result = await fetchJson(apiUrl("/research/pats"), parseIssuedPat, {
  credentials: "include", method: "POST",
  headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "Idempotency-Key": key },
  // 요청은 계약 필드만 복사한다. 비밀 값·추가 속성은 보내지 않는다.
  body: JSON.stringify({ name: body.name, scope: body.scope, expiryDays: body.expiryDays }),
  });
  if (result.ok && !((result.status === 201 && result.data.secretAvailable && !result.data.replayed) || (result.status === 200 && !result.data.secretAvailable && result.data.replayed))) return { ok: false as const, failure: { kind: "invalid" as const, status: result.status } };
  return result;
};
export const revokePat = (id: string, csrfToken: string) => fetchJson(apiUrl(`/research/pats/${encodeURIComponent(id)}`), parseRevoke, {
  credentials: "include", method: "DELETE", headers: { "X-CSRF-Token": csrfToken },
});

export function patStatus(pat: Pat, now: number): "active" | "expired" | "revoked" {
  return pat.revokedAt ? "revoked" : Date.parse(pat.expiresAt) <= now ? "expired" : "active";
}
export const PAT_SCOPE_LABELS: Record<PatScope, string> = { read: "읽기", write: "쓰기(읽기 포함)" };
export const PAT_EFFECTIVE_LABELS: Record<Pat["effectiveScope"], string> = { public: "공개 콘텐츠 읽기만", read: "내 데이터 읽기", write: "내 데이터 읽기·저장·수정·보관", none: "사용 권한 없음" };
export function patFailureMessage(failure: FetchFailure): string {
  if (failure.kind === "http") {
    if (failure.status === 401) return "로그인 상태를 다시 확인한 뒤 시도하세요.";
    if (failure.code === "pat_limit") return "활성 토큰은 최대 10개입니다. 기존 토큰을 폐기한 뒤 발급하세요.";
    if (failure.code === "idempotency_conflict") return "앞선 요청과 내용이 다릅니다. 토큰 목록에서 발급 결과를 먼저 확인하세요.";
    if (failure.status === 429) return "요청이 많습니다. 잠시 뒤 직접 다시 시도하세요.";
    if (failure.status === 403) return "현재 로그인 또는 계정 권한으로 처리할 수 없습니다. 로그인 상태와 승인을 확인하세요.";
    if (failure.status === 422) return "이름과 권한, 만료 기간을 확인하세요.";
  }
  return "요청 결과를 확인하지 못했습니다. 자동으로 다시 실행하지 않습니다.";
}
