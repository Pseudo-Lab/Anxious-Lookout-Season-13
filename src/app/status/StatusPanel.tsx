"use client";

import { useEffect, useState, type ReactNode } from "react";
import { API_ENABLED, BASE_PATH } from "@/lib/constants";
import { apiUrl, describeFailure, fetchJson, type FetchResult } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { ROLE_LABELS } from "@/lib/auth/api";
import { formatDateTime } from "@/lib/research/format";

interface VersionInfo {
  sha: string;
  builtAt: string;
}

function parseVersion(body: unknown): VersionInfo | null {
  const b = body as { sha?: unknown; builtAt?: unknown } | null;
  if (typeof b?.sha !== "string" || !/^[0-9a-f]{40}$/.test(b.sha)) return null;
  if (typeof b.builtAt !== "string") return null;
  return { sha: b.sha, builtAt: b.builtAt };
}

function parseHealth(body: unknown): true | null {
  return (body as { status?: unknown } | null)?.status === "ok" ? true : null;
}

interface Checks {
  web: FetchResult<VersionInfo>;
  // API가 없는 배포(정적 export)에서는 null
  health: FetchResult<true> | null;
  // M3 연구 기능 저장소(schema/권한) 준비 상태. M2 health와 별개다.
  research: FetchResult<true> | null;
  api: FetchResult<VersionInfo> | null;
}

function Row({ label, ok, children }: { label: string; ok: boolean | null; children: ReactNode }) {
  const dot = ok === null ? "bg-stone-300" : ok ? "bg-emerald-500" : "bg-red-500";
  return (
    <div className="flex items-start gap-3 border-b border-stone-100 py-4 last:border-0">
      <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-stone-900">{label}</p>
        <div className="mt-1 break-all text-sm text-stone-900">{children}</div>
      </div>
    </div>
  );
}

/** #119 H1: 커밋 앞 7자와 전체 해시 복사. 복사가 막힌 브라우저에서는 전체 해시를 보이고 직접 복사하게 한다. */
function CommitSha({ label, sha }: { label: string; sha: string }) {
  const [note, setNote] = useState("");
  const [full, setFull] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(sha);
      setNote("전체 해시를 복사했습니다.");
    } catch {
      setFull(true);
      setNote("복사하지 못했습니다. 전체 해시를 직접 복사하세요.");
    }
  }
  return (
    <span data-commit-sha={label} className="flex flex-wrap items-center gap-2">
      <code title={sha} className="font-mono text-xs">
        {full ? sha : sha.slice(0, 7)}
      </code>
      <button type="button" onClick={() => void copy()} className="text-xs font-medium text-indigo-600 underline hover:text-indigo-800">
        복사<span className="sr-only"> ({label} 전체 커밋 해시)</span>
      </button>
      {note && (
        <span role="status" className="text-xs text-stone-600">
          {note}
        </span>
      )}
    </span>
  );
}

function VersionText({ label, result }: { label: string; result: FetchResult<VersionInfo> }) {
  if (!result.ok) return <>{describeFailure(result.failure)}</>;
  return (
    <>
      <CommitSha label={label} sha={result.data.sha} />
      {/* #119 H2·B7: 현지 시각 */}
      <span className="mt-1 block text-xs text-stone-600">
        빌드 <time dateTime={result.data.builtAt}>{formatDateTime(result.data.builtAt)}</time>
      </span>
    </>
  );
}

async function loadChecks(): Promise<Checks> {
  const [web, health, research, api] = await Promise.all([
    fetchJson(`${BASE_PATH}/version.json`, parseVersion),
    API_ENABLED ? fetchJson(apiUrl("/health"), parseHealth) : null,
    API_ENABLED ? fetchJson(apiUrl("/research/health"), parseHealth) : null,
    API_ENABLED ? fetchJson(apiUrl("/version"), parseVersion) : null,
  ]);
  return { web, health, research, api };
}

const NO_API = "이 배포(정적 사이트)에는 API 서버가 없습니다.";

export default function StatusPanel() {
  const [checks, setChecks] = useState<Checks | null>(null);
  const auth = useAuth();

  useEffect(() => {
    let active = true;
    void loadChecks().then((result) => {
      if (active) setChecks(result);
    });
    return () => {
      active = false;
    };
  }, []);

  function recheck() {
    setChecks(null);
    void loadChecks().then(setChecks);
    void auth.refresh();
  }

  let healthText: ReactNode = "확인 중...";
  if (checks?.health === null) {
    healthText = NO_API;
  } else if (checks) {
    if (checks.health.ok) healthText = "정상 (요청을 받을 준비가 되었습니다)";
    else if (checks.health.failure.kind === "http" && checks.health.failure.code === "not_ready")
      healthText = "준비되지 않음 (not_ready)";
    else healthText = describeFailure(checks.health.failure);
  }

  let researchText: ReactNode = "확인 중...";
  if (checks?.research === null) {
    researchText = NO_API;
  } else if (checks) {
    if (checks.research.ok) researchText = "정상 (자료·문서 저장소 준비됨)";
    else if (checks.research.failure.kind === "http" && checks.research.failure.code === "not_ready")
      researchText = "준비되지 않음 (not_ready)";
    else researchText = describeFailure(checks.research.failure);
  }

  let accountOk: boolean | null = null;
  let accountText: ReactNode = "확인 중...";
  if (auth.status === "authenticated" && auth.user) {
    accountOk = true;
    accountText = (
      <>
        {/* #119 H3: 값마다 항목 이름 */}
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          <dt className="text-stone-600">계정</dt>
          <dd className="font-medium text-stone-900">{auth.user.login}</dd>
          <dt className="text-stone-600">역할</dt>
          <dd>{ROLE_LABELS[auth.user.role]}</dd>
          <dt className="text-stone-600">승인</dt>
          <dd>
            {auth.user.isApproved ? (
              "승인됨"
            ) : (
              <span className="text-amber-700">승인 대기 — 관리자 승인 후 회원 기능을 이용할 수 있습니다.</span>
            )}
          </dd>
        </dl>
      </>
    );
  } else if (auth.status === "unauthenticated") {
    accountOk = null;
    accountText = "로그인하지 않았습니다.";
  } else if (auth.status === "disabled") {
    accountText = "이 배포에서는 로그인을 제공하지 않습니다.";
  } else if (auth.status === "error" && auth.failure) {
    accountOk = false;
    accountText = `로그인 상태를 확인하지 못했습니다. ${describeFailure(auth.failure)}`;
  }

  return (
    <>
      <div className="rounded-2xl bg-white px-6 shadow-sm ring-1 ring-stone-200/60">
        <Row label="웹 버전" ok={checks ? checks.web.ok : null}>
          {checks ? <VersionText label="웹" result={checks.web} /> : "확인 중..."}
        </Row>
        <Row label="API 상태" ok={checks?.health ? checks.health.ok : null}>
          {healthText}
        </Row>
        <Row label="연구 기능" ok={checks?.research ? checks.research.ok : null}>
          {researchText}
        </Row>
        <Row label="API 버전" ok={checks?.api ? checks.api.ok : null}>
          {!checks ? "확인 중..." : checks.api === null ? NO_API : <VersionText label="API" result={checks.api} />}
        </Row>
        <Row label="로그인 상태" ok={accountOk}>
          {accountText}
        </Row>
      </div>
      <button
        onClick={recheck}
        disabled={checks === null}
        className="mt-6 rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm text-stone-900 transition-colors hover:bg-stone-50 disabled:opacity-50"
      >
        다시 확인
      </button>
    </>
  );
}
