// 결과를 확인하지 못한 세션 메시지 전송(응답 유실·timeout·5xx).
// 같은 의도의 재전송이 새 모델 turn을 만들지 않도록 처음 요청의 Idempotency-Key와 본문(expectedVersion 포함)을
// 그대로 다시 보낸다. 페이지를 새로 열거나 같은 계정으로 다시 로그인해도 이어지도록 이 탭의 sessionStorage에
// 계정별로 두고, 결과가 확정되면 지운다. 인증 정보는 담지 않는다(CSRF nonce·session cookie 제외).

export interface PendingSend {
  key: string;
  text: string;
  expectedVersion: number;
}

const PREFIX = "research:pending-send:";
const storageKey = (accountId: string, sessionId: string) => `${PREFIX}${accountId}:${sessionId}`;

export function loadPendingSend(accountId: string, sessionId: string): PendingSend | null {
  try {
    const raw = sessionStorage.getItem(storageKey(accountId, sessionId));
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<PendingSend>;
    if (typeof v.key === "string" && typeof v.text === "string" && Number.isInteger(v.expectedVersion)) {
      return { key: v.key, text: v.text, expectedVersion: v.expectedVersion as number };
    }
  } catch {
    // 저장소를 쓸 수 없으면 메모리 상태만 사용한다.
  }
  return null;
}

export function savePendingSend(accountId: string, sessionId: string, pending: PendingSend): void {
  try {
    sessionStorage.setItem(storageKey(accountId, sessionId), JSON.stringify(pending));
  } catch {}
}

export function clearPendingSend(accountId: string, sessionId: string): void {
  try {
    sessionStorage.removeItem(storageKey(accountId, sessionId));
  } catch {}
}

/** 명시적 로그아웃 시 이 탭에 남은 미확인 전송(입력 내용 포함)을 모두 지운다. */
export function clearAllPendingSends(): void {
  try {
    for (const k of Object.keys(sessionStorage)) if (k.startsWith(PREFIX)) sessionStorage.removeItem(k);
  } catch {}
}
