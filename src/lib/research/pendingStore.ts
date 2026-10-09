// 결과를 확인하지 못한 변경 요청 보관소(이 탭의 sessionStorage, 계정별).
// 응답 유실 → 401 → 같은 계정 재로그인처럼 화면이 다시 그려져도 처음 요청(Idempotency-Key·본문)을 복구해
// 같은 요청으로 다시 보내면 서버가 원래 결과를 돌려주므로 중복 생성되지 않는다.
// 다른 계정에는 보이지 않으며(키에 accountId 포함), 명시적 로그아웃 때 모두 지운다. 인증 정보는 담지 않는다.
//
// 로그아웃 전에 시작된 요청이 늦게 끝나 다시 쓰지 않도록 보관소 세대(generation)를 둔다. 명시적 로그아웃은 세대를 올리고,
// 이전 세대에서 시작한 요청의 쓰기는 무시한다(writeRequest). 401 재인증은 세대를 바꾸지 않아 보관이 유지된다.
// 늦은 응답은 같은 페이지(JS 실행 환경)에서만 오므로 세대는 메모리에 두고, 로그아웃 뒤 저장소는 완전히 비운다.

const PREFIX = "research:pending:";
let generation = 0;

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

export function storeGeneration(): number {
  return generation;
}

/** 명시적 로그아웃 시 이 탭에 남은 미확인 요청(입력 내용 포함)을 모두 지우고 세대를 올린다. */
export function clearAllRequests(): void {
  generation += 1;
  try {
    for (const k of Object.keys(sessionStorage)) if (k.startsWith(PREFIX)) sessionStorage.removeItem(k);
  } catch {}
}

/** 쓰기 주체가 알고 있는 범위. 시작한 세대와, 자신이 만들었거나 불러온 key들. */
export interface WriteGuard {
  generation: number;
  knownKeys: ReadonlySet<string>;
}

/**
 * 조건부 보관. 세대가 바뀌었거나(로그아웃 이후), 같은 범위에 이 주체가 모르는 더 새 요청이 있으면 쓰지 않는다.
 */
export function writeRequest(accountId: string, scope: string, request: StoredRequest, guard: WriteGuard): void {
  if (storeGeneration() !== guard.generation) return;
  const existing = loadRequest(accountId, scope);
  if (existing && existing.key !== request.key && !guard.knownKeys.has(existing.key)) return;
  saveRequest(accountId, scope, request);
}

/** 조건부 삭제. 보관된 요청이 이 주체가 아는 key일 때만 지운다(다른 곳에서 만든 새 요청은 남긴다). */
export function removeRequest(accountId: string, scope: string, guard: WriteGuard): void {
  const existing = loadRequest(accountId, scope);
  if (existing && guard.knownKeys.has(existing.key)) clearRequest(accountId, scope);
}
