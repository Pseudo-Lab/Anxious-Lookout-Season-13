import { BASE_PATH } from "@/lib/constants";

// 브라우저는 같은 origin의 `<base>/api`(FastAPI)를 직접 호출한다. api-contracts/m2-api-v1.md 참고.
export function apiUrl(path: `/${string}`): string {
  return `${BASE_PATH}/api${path}`;
}

export type FetchFailure =
  | { kind: "network" }
  | { kind: "timeout" }
  // 200이 아닌 응답. JSON 오류 본문이면 code를 담는다(Traefik 502 HTML 등은 code 없음).
  // fields는 422 validation_error의 필드별 안내(입력값은 포함되지 않음).
  | { kind: "http"; status: number; code?: string; fields?: FieldError[] }
  // 2xx이지만 JSON이 아니거나 형식이 맞지 않음
  | { kind: "invalid"; status: number };

export interface FieldError {
  path: string;
  message: string;
}

export type FetchResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; failure: FetchFailure };

const DEFAULT_TIMEOUT_MS = 5000;

function isJson(res: Response): boolean {
  return (res.headers.get("content-type") ?? "").toLowerCase().includes("application/json");
}

async function readError(res: Response): Promise<{ code?: string; fields?: FieldError[] }> {
  if (!isJson(res)) return {};
  try {
    const body: unknown = await res.json();
    const error = (body as { error?: { code?: unknown; fields?: unknown } } | null)?.error;
    const code = typeof error?.code === "string" ? error.code : undefined;
    const fields = Array.isArray(error?.fields)
      ? error.fields.filter(
          (f): f is FieldError =>
            typeof (f as FieldError)?.path === "string" && typeof (f as FieldError)?.message === "string"
        )
      : undefined;
    return { code, fields: fields?.length ? fields : undefined };
  } catch {
    return {};
  }
}

/**
 * JSON을 요청하고 결과를 실패 종류별로 나눠 돌려준다. 예외를 던지지 않는다.
 * parse가 null을 돌려주면 형식 불일치(invalid)로 본다.
 */
export async function fetchJson<T>(
  url: string,
  parse: (body: unknown) => T | null,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS
): Promise<FetchResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...init,
      headers: { Accept: "application/json", ...init.headers },
      signal: controller.signal,
    });
    if (!res.ok) {
      return { ok: false, failure: { kind: "http", status: res.status, ...(await readError(res)) } };
    }
    if (!isJson(res)) {
      return { ok: false, failure: { kind: "invalid", status: res.status } };
    }
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      return { ok: false, failure: { kind: "invalid", status: res.status } };
    }
    const data = parse(body);
    if (data === null) {
      return { ok: false, failure: { kind: "invalid", status: res.status } };
    }
    return { ok: true, status: res.status, data };
  } catch {
    return { ok: false, failure: { kind: controller.signal.aborted ? "timeout" : "network" } };
  } finally {
    clearTimeout(timer);
  }
}

export function describeFailure(failure: FetchFailure): string {
  switch (failure.kind) {
    case "network":
      return "서버에 연결할 수 없습니다.";
    case "timeout":
      return "응답 시간이 초과되었습니다.";
    case "invalid":
      return `응답 형식이 올바르지 않습니다 (HTTP ${failure.status}).`;
    case "http":
      return failure.code
        ? `요청이 실패했습니다 (HTTP ${failure.status}, ${failure.code}).`
        : `요청이 실패했습니다 (HTTP ${failure.status}).`;
  }
}
