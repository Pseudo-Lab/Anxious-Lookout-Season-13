// 결과를 확인하지 못한 변경 요청 보관소(이 탭의 sessionStorage, 계정별).
// 응답 유실 → 401 → 같은 계정 재로그인처럼 화면이 다시 그려져도 처음 요청(Idempotency-Key·본문)을 복구해
// 같은 요청으로 다시 보내면 서버가 원래 결과를 돌려주므로 중복 생성되지 않는다.
// 다른 계정에는 보이지 않으며(키에 accountId 포함), 명시적 로그아웃 때 모두 지운다. 인증 정보는 담지 않는다.

const PREFIX = "research:pending:";

export interface StoredRequest<T = unknown> {
  fingerprint: string;
  key: string;
  body: T;
}

const storageKey = (accountId: string, scope: string) => `${PREFIX}${accountId}:${scope}`;

export function loadRequest<T>(accountId: string, scope: string): StoredRequest<T> | null {
  try {
    const raw = sessionStorage.getItem(storageKey(accountId, scope));
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<StoredRequest<T>>;
    if (typeof v.fingerprint === "string" && typeof v.key === "string" && "body" in v) {
      return { fingerprint: v.fingerprint, key: v.key, body: v.body as T };
    }
  } catch {
    // 저장소를 쓸 수 없으면 메모리 상태만 사용한다.
  }
  return null;
}

export function saveRequest(accountId: string, scope: string, request: StoredRequest): void {
  try {
    sessionStorage.setItem(storageKey(accountId, scope), JSON.stringify(request));
  } catch {}
}

export function clearRequest(accountId: string, scope: string): void {
  try {
    sessionStorage.removeItem(storageKey(accountId, scope));
  } catch {}
}

/** 명시적 로그아웃 시 이 탭에 남은 미확인 요청(입력 내용 포함)을 모두 지운다. */
export function clearAllRequests(): void {
  try {
    for (const k of Object.keys(sessionStorage)) if (k.startsWith(PREFIX)) sessionStorage.removeItem(k);
  } catch {}
}
