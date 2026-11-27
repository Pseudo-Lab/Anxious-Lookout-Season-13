"use client";

import { useState, type FormEvent } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { ROLE_LABELS } from "@/lib/auth/api";
import { listAdminAccounts, listAdminAudit, setMembership, type AdminAccountFilter, type AuditEntry } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import type { AdminAccount } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import AuthGate from "@/components/research/AuthGate";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

type Action = "grant" | "demote" | "unapprove";

// #119 L2: 권한을 주는 동작과 빼는 동작을 색으로 나눈다. 빼는 동작은 확인 칸에 무엇을 잃는지 알리고 붉은 적용 버튼을 쓴다(사유 필수).
const ACTIONS: Record<
  Action,
  { label: string; tone: string; removes: boolean; effect: string; input: (a: AdminAccount) => { role: "editor" | "commenter"; isApproved: boolean } }
> = {
  // 개인 자료·문서·Codex 이용 대상(승인된 편집자)으로 지정
  grant: {
    label: "편집자로 지정",
    tone: "border-emerald-300 text-emerald-800 hover:bg-emerald-50",
    removes: false,
    effect: "내 연구·글 쓰기·대화 기능을 쓸 수 있게 됩니다.",
    input: () => ({ role: "editor", isApproved: true }),
  },
  // 승인은 유지하고 편집자 역할만 해제
  demote: {
    label: "편집자 해제",
    tone: "border-red-300 text-red-700 hover:bg-red-50",
    removes: true,
    effect: "내 연구·대화 기능을 더 이상 쓸 수 없습니다(쓴 자료·문서는 남습니다).",
    input: (a) => ({ role: "commenter", isApproved: a.isApproved }),
  },
  // 승인 자체를 취소
  unapprove: {
    label: "승인 취소",
    tone: "border-red-300 text-red-700 hover:bg-red-50",
    removes: true,
    effect: "회원 기능(글 쓰기·내 연구·대화)을 모두 쓸 수 없게 됩니다.",
    input: () => ({ role: "commenter", isApproved: false }),
  },
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
            {/* #119 L4: 숫자 ID 대신 GitHub 프로필(마지막 로그인 때의 login) */}
            <a
              href={`https://github.com/${encodeURIComponent(account.login)}`}
              target="_blank"
              rel="noopener noreferrer"
              title={`GitHub ID ${account.githubId}`}
              className="hover:text-indigo-700 hover:underline"
            >
              {account.login}
              <span className="sr-only"> (GitHub 프로필, 새 창)</span>
            </a>{" "}
            {isSelf && <span className="text-xs text-stone-500">(나)</span>}
          </p>
          <p className="text-xs text-stone-600">
            {ROLE_LABELS[account.role]} ·{" "}
            {account.isApproved ? "승인됨" : <span className="text-amber-700">승인 대기</span>} · 변경 {formatDateTime(account.updatedAt)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {available.map((a) => (
            <button
              key={a}
              onClick={() => {
                setAction(a);
                setDone(null);
                setFailure(null);
              }}
              aria-label={`${ACTIONS[a].label}: ${account.login}`}
              className={`rounded-md border px-2 py-1 ${ACTIONS[a].tone}`}
            >
              {ACTIONS[a].label}
            </button>
          ))}
          {available.length === 0 && (
            <span className="text-stone-500">{isSelf ? "본인 계정은 바꿀 수 없습니다" : "관리자 계정은 바꿀 수 없습니다"}</span>
          )}
        </div>
      </div>
      {action && (
        <form
          onSubmit={submit}
          className={`space-y-2 rounded-lg p-3 ring-1 ${ACTIONS[action].removes ? "bg-red-50 ring-red-200" : "bg-stone-50 ring-stone-200"}`}
        >
          <p className="text-sm text-stone-900">
            <span className="font-medium">{account.login}</span> — {ACTIONS[action].label}
          </p>
          <p className={`text-xs ${ACTIONS[action].removes ? "text-red-800" : "text-stone-700"}`}>
            {ACTIONS[action].effect} 대상 사용자는 다시 로그인해야 합니다.
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
            <button
              type="submit"
              disabled={busy || !reason.trim()}
              className={`rounded-lg px-3 py-1 text-white disabled:opacity-50 ${ACTIONS[action].removes ? "bg-red-600 hover:bg-red-700" : "bg-indigo-600 hover:bg-indigo-700"}`}
            >
              {busy ? "적용 중..." : "적용"}
            </button>
            <button type="button" onClick={() => setAction(null)} className="rounded-lg px-3 py-1 text-stone-900 hover:bg-stone-100">
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

const ROLE_FILTERS = [
  { value: "", label: "모든 역할" },
  { value: "admin", label: ROLE_LABELS.admin },
  { value: "editor", label: ROLE_LABELS.editor },
  { value: "commenter", label: ROLE_LABELS.commenter },
] as const;

function AccountSection({
  title,
  filter,
  rev,
  selfId,
  emptyText,
  highlight = false,
}: {
  title: string;
  filter: AdminAccountFilter;
  rev: number;
  selfId: string;
  emptyText: string;
  highlight?: boolean;
}) {
  const list = usePagedList(`admin-accounts:${rev}:${JSON.stringify(filter)}`, (cursor) => listAdminAccounts(cursor, filter));
  const headingId = `admin-${title}`;
  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <h2 id={headingId} className="text-base font-semibold text-stone-900">
        {title} {list.total !== null && <span className="text-sm font-normal text-stone-600">· {list.total}명</span>}
      </h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="회원 목록을 불러오지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <p className="text-sm text-stone-600">{emptyText}</p>}
      {list.items.length > 0 && (
        <ul
          className={`divide-y divide-stone-100 rounded-2xl bg-white shadow-sm ring-1 ${highlight ? "ring-amber-300" : "ring-stone-200/60"}`}
        >
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

/** #119 L1·L5: 승인 대기 회원을 맨 위에 따로 모으고, 나머지는 검색·역할로 거른다(서버 필터, issue119 §L1). */
function AccountList({ selfId }: { selfId: string }) {
  const [rev, setRev] = useState(0);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [role, setRole] = useState<"" | AdminAccount["role"]>("");
  const filtered = !!(q || role);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <form
          role="search"
          aria-label="회원 찾기"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(text.trim().slice(0, 100));
          }}
          className="flex flex-wrap gap-2"
        >
          <input
            type="search"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              if (!e.target.value) setQ("");
            }}
            placeholder="GitHub 이름으로 찾기"
            aria-label="GitHub 이름으로 찾기"
            className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
          />
          <button type="submit" className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm hover:bg-stone-50">
            찾기
          </button>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as typeof role)}
            aria-label="역할로 거르기"
            className="rounded-lg border border-stone-300 px-2 py-1.5 text-sm"
          >
            {ROLE_FILTERS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </form>
        <button onClick={() => setRev((r) => r + 1)} className="text-sm text-stone-500 hover:text-stone-800">
          새로고침
        </button>
      </div>
      <AccountSection
        title="승인 대기"
        filter={{ approved: false, ...(role ? { role } : {}), ...(q ? { q } : {}) }}
        rev={rev}
        selfId={selfId}
        emptyText={filtered ? "조건에 맞는 승인 대기 회원이 없습니다." : "승인을 기다리는 회원이 없습니다."}
        highlight
      />
      <AccountSection
        title="승인된 회원"
        filter={{ approved: true, ...(role ? { role } : {}), ...(q ? { q } : {}) }}
        rev={rev}
        selfId={selfId}
        emptyText={filtered ? "조건에 맞는 회원이 없습니다." : "승인된 회원이 없습니다."}
      />
    </div>
  );
}

const stateText = (s: AuditEntry["before"]) =>
  `${ROLE_LABELS[s.role as AdminAccount["role"]] ?? s.role} · ${s.isApproved ? "승인" : "미승인"}`;

/** #119 L3: 권한 변경 감사 기록(읽기 전용). 접어 두고 펼칠 때만 불러온다. */
function AuditLog() {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)} className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-stone-200/60">
      <summary className="cursor-pointer text-base font-semibold text-stone-900">감사 기록</summary>
      {open && <AuditEntries />}
    </details>
  );
}

function AuditEntries() {
  const list = usePagedList("admin-audit", (cursor) => listAdminAudit(cursor));
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs text-stone-600">회원 권한을 바꾼 기록입니다. 최신순이며 고칠 수 없습니다.</p>
      {list.failure && <ErrorNotice failure={list.failure} prefix="감사 기록을 불러오지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <p className="text-sm text-stone-600">아직 기록이 없습니다.</p>}
      <ol className="divide-y divide-stone-100 text-sm">
        {list.items.map((e) => (
          <li key={e.id} className="py-2">
            <p className="text-stone-900">
              <span className="font-medium">{e.account.login}</span>: {stateText(e.before)} → <span className="font-medium">{stateText(e.after)}</span>
            </p>
            <p className="text-xs text-stone-600">
              <time dateTime={e.createdAt}>{formatDateTime(e.createdAt)}</time> · 변경한 사람{" "}
              {e.actor.kind === "account" ? (e.actor.login ?? "(알 수 없는 계정)") : `운영 도구(${e.actor.label})`}
            </p>
            <p className="mt-0.5 whitespace-pre-wrap text-xs text-stone-800">사유: {e.reason}</p>
          </li>
        ))}
      </ol>
      {list.loading && <p className="text-sm text-stone-500">불러오는 중...</p>}
      {list.nextCursor && !list.loading && (
        <button onClick={() => void list.loadMore()} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
    </div>
  );
}

function AdminHome() {
  const { user } = useAuth();
  if (!user) return null;
  // 안내용 확인이며 실제 허용 여부는 서버(admin API 403)가 판단한다.
  if (!(user.isApproved && user.role === "admin")) {
    return <Notice tone="info">관리 화면은 승인된 관리자만 이용할 수 있습니다.</Notice>;
  }
  return (
    <div className="space-y-8">
      <AccountList selfId={user.accountId} />
      <AuditLog />
    </div>
  );
}

export default function AdminPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-2 text-2xl font-bold text-stone-900">관리</h1>
      <p className="mb-6 text-sm text-stone-500">
        승인된 편집자는 개인 자료·문서와 대화 기능을 이용할 수 있습니다. 변경은 사유와 함께 아래 감사 기록에 남고, 대상 사용자는 다시
        로그인해야 합니다.
      </p>
      <AuthGate>
        <AdminHome />
      </AuthGate>
    </div>
  );
}
