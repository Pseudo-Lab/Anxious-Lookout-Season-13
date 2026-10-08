"use client";

import { useState, type FormEvent } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { ROLE_LABELS } from "@/lib/auth/api";
import { listAdminAccounts, setMembership } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import type { AdminAccount } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import AuthGate from "@/components/research/AuthGate";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

type Action = "grant" | "demote" | "unapprove";

const ACTIONS: Record<Action, { label: string; tone: string; input: (a: AdminAccount) => { role: "editor" | "commenter"; isApproved: boolean } }> = {
  // 개인 자료·문서·Codex 이용 대상(승인된 편집자)으로 지정
  grant: { label: "편집자로 지정", tone: "text-emerald-700", input: () => ({ role: "editor", isApproved: true }) },
  // 승인은 유지하고 편집자 역할만 해제
  demote: { label: "편집자 해제", tone: "text-amber-700", input: (a) => ({ role: "commenter", isApproved: a.isApproved }) },
  // 승인 자체를 취소
  unapprove: { label: "승인 취소", tone: "text-red-700", input: () => ({ role: "commenter", isApproved: false }) },
};

function AccountRow({ initial, selfId }: { initial: AdminAccount; selfId: string }) {
  const [account, setAccount] = useState(initial);
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const { run, busy } = useMutation();

  const isSelf = account.accountId === selfId;
  const isAdmin = account.role === "admin";
  const isEditor = account.role === "editor" && account.isApproved;
  const available: Action[] = isSelf || isAdmin ? [] : isEditor ? ["demote", "unapprove"] : account.isApproved ? ["grant", "unapprove"] : ["grant"];

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!action || !reason.trim()) return;
    const input = { ...ACTIONS[action].input(account), reason: reason.trim() };
    const res = await run(`membership:${account.accountId}:${account.version}:${JSON.stringify(input)}`, (ctx) =>
      setMembership(account.accountId, input, account.version, ctx)
    );
    if (res.ok) {
      setAccount(res.data);
      setDone(`${ACTIONS[action].label} 완료. 대상 사용자는 다시 로그인해야 합니다.`);
      setAction(null);
      setReason("");
      setFailure(null);
    } else {
      setFailure(res.failure);
    }
  }

  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium text-stone-900">
            {account.login} {isSelf && <span className="text-xs text-stone-500">(나)</span>}
          </p>
          <p className="text-xs text-stone-500">
            GitHub {account.githubId} · {ROLE_LABELS[account.role]} ·{" "}
            {account.isApproved ? "승인됨" : <span className="text-amber-700">승인 대기</span>} · 변경 {formatDateTime(account.updatedAt)}
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs">
          {available.map((a) => (
            <button
              key={a}
              onClick={() => {
                setAction(a);
                setDone(null);
                setFailure(null);
              }}
              aria-label={`${ACTIONS[a].label}: ${account.login}`}
              className={`${ACTIONS[a].tone} hover:underline`}
            >
              {ACTIONS[a].label}
            </button>
          ))}
          {available.length === 0 && (
            <span className="text-stone-400">{isSelf ? "본인 계정은 바꿀 수 없습니다" : "관리자 계정은 바꿀 수 없습니다"}</span>
          )}
        </div>
      </div>
      {action && (
        <form onSubmit={submit} className="space-y-2 rounded-lg bg-stone-50 p-3">
          <p className="text-sm text-stone-700">
            <span className="font-medium">{account.login}</span> — {ACTIONS[action].label}
          </p>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            required
            maxLength={500}
            placeholder="변경 사유 (감사 기록에 남습니다)"
            aria-label="변경 사유"
            className="w-full rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
          />
          <div className="flex gap-2 text-sm">
            <button type="submit" disabled={busy || !reason.trim()} className="rounded-lg bg-indigo-600 px-3 py-1 text-white disabled:opacity-50">
              {busy ? "적용 중..." : "적용"}
            </button>
            <button type="button" onClick={() => setAction(null)} className="rounded-lg px-3 py-1 text-stone-600 hover:bg-stone-100">
              취소
            </button>
          </div>
        </form>
      )}
      {done && <Notice tone="success">{done}</Notice>}
      {failure && <ErrorNotice failure={failure} prefix="변경하지 못했습니다." />}
    </li>
  );
}

function AccountList({ selfId }: { selfId: string }) {
  const [rev, setRev] = useState(0);
  const list = usePagedList(`admin-accounts:${rev}`, (cursor) => listAdminAccounts(cursor));
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-stone-800">회원</h2>
        <button onClick={() => setRev((r) => r + 1)} className="text-sm text-stone-500 hover:text-stone-800">
          새로고침
        </button>
      </div>
      {list.failure && <ErrorNotice failure={list.failure} prefix="회원 목록을 불러오지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <Notice tone="info">회원이 없습니다.</Notice>}
      {list.items.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-2xl bg-white shadow-sm ring-1 ring-stone-200/60">
          {list.items.map((a) => (
            <AccountRow key={`${a.accountId}:${a.version}`} initial={a} selfId={selfId} />
          ))}
        </ul>
      )}
      {list.loading && <p className="text-sm text-stone-500">불러오는 중...</p>}
      {list.nextCursor && !list.loading && (
        <button onClick={() => void list.loadMore()} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
    </section>
  );
}

function AdminHome() {
  const { user } = useAuth();
  if (!user) return null;
  // 안내용 확인이며 실제 허용 여부는 서버(admin API 403)가 판단한다.
  if (!(user.isApproved && user.role === "admin")) {
    return <Notice tone="info">관리 화면은 승인된 관리자만 이용할 수 있습니다.</Notice>;
  }
  return <AccountList selfId={user.accountId} />;
}

export default function AdminPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-2 text-2xl font-bold text-stone-900">관리</h1>
      <p className="mb-6 text-sm text-stone-500">
        승인된 편집자는 개인 자료·문서와 Codex 질문을 이용할 수 있습니다. 변경은 사유와 함께 감사 기록에 남고, 대상 사용자는 다시
        로그인해야 합니다.
      </p>
      <AuthGate>
        <AdminHome />
      </AuthGate>
    </div>
  );
}
