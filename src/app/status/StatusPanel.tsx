"use client";

import { useEffect, useState, type ReactNode } from "react";
import { BASE_PATH } from "@/lib/constants";
import { apiUrl, describeFailure, fetchJson, type FetchResult } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { ROLE_LABELS } from "@/lib/auth/api";

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
  health: FetchResult<true>;
  api: FetchResult<VersionInfo>;
}

function Row({ label, ok, children }: { label: string; ok: boolean | null; children: ReactNode }) {
  const dot = ok === null ? "bg-stone-300" : ok ? "bg-emerald-500" : "bg-red-500";
  return (
    <div className="flex items-start gap-3 border-b border-stone-100 py-4 last:border-0">
      <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-stone-800">{label}</p>
        <div className="mt-1 break-all text-sm text-stone-600">{children}</div>
      </div>
    </div>
  );
}

function VersionText({ result }: { result: FetchResult<VersionInfo> }) {
  if (!result.ok) return <>{describeFailure(result.failure)}</>;
  return (
    <>
      <code className="font-mono text-xs">{result.data.sha}</code>
      <span className="block text-xs text-stone-500">빌드 {result.data.builtAt}</span>
    </>
  );
}

async function loadChecks(): Promise<Checks> {
  const [web, health, api] = await Promise.all([
    fetchJson(`${BASE_PATH}/version.json`, parseVersion),
    fetchJson(apiUrl("/health"), parseHealth),
    fetchJson(apiUrl("/version"), parseVersion),
  ]);
  return { web, health, api };
}

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
  if (checks) {
    if (checks.health.ok) healthText = "정상 (요청을 받을 준비가 되었습니다)";
    else if (checks.health.failure.kind === "http" && checks.health.failure.code === "not_ready")
      healthText = "준비되지 않음 (not_ready)";
    else healthText = describeFailure(checks.health.failure);
  }

  let accountOk: boolean | null = null;
  let accountText: ReactNode = "확인 중...";
  if (auth.status === "authenticated" && auth.user) {
    accountOk = true;
    accountText = (
      <>
        <span className="font-medium text-stone-900">{auth.user.login}</span> ·{" "}
        {ROLE_LABELS[auth.user.role]} ·{" "}
        {auth.user.isApproved ? (
          "승인됨"
        ) : (
          <span className="text-amber-700">승인 대기 — 관리자 승인 후 회원 기능을 이용할 수 있습니다.</span>
        )}
      </>
    );
  } else if (auth.status === "unauthenticated") {
    accountOk = null;
    accountText = "로그인하지 않았습니다.";
  } else if (auth.status === "error" && auth.failure) {
    accountOk = false;
    accountText = `로그인 상태를 확인하지 못했습니다. ${describeFailure(auth.failure)}`;
  }

  return (
    <>
      <div className="rounded-2xl bg-white px-6 shadow-sm ring-1 ring-stone-200/60">
        <Row label="웹 버전" ok={checks ? checks.web.ok : null}>
          {checks ? <VersionText result={checks.web} /> : "확인 중..."}
        </Row>
        <Row label="API 상태" ok={checks ? checks.health.ok : null}>
          {healthText}
        </Row>
        <Row label="API 버전" ok={checks ? checks.api.ok : null}>
          {checks ? <VersionText result={checks.api} /> : "확인 중..."}
        </Row>
        <Row label="계정" ok={accountOk}>
          {accountText}
        </Row>
      </div>
      <button
        onClick={recheck}
        disabled={checks === null}
        className="mt-6 rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm text-stone-600 transition-colors hover:bg-stone-50 disabled:opacity-50"
      >
        다시 확인
      </button>
    </>
  );
}
