"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { flushSync } from "react-dom";
import { useAuth } from "@/hooks/useAuth";
import { issuePat, listPats, revokePat, patStatus, patFailureMessage, type IssuedPat, type PatInput } from "@/lib/auth/pats";
import { isUncertain, uuidV4 } from "@/lib/research/idempotency";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import { inputClass } from "@/components/research/ItemForms";
import Notice from "@/components/research/Notice";

function OneTimeValue({ token, onClose }: { token: string; onClose: () => void }) {
  const fieldId = useId();
  const [note, setNote] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  return (
    <section aria-label="발급한 토큰 값" className="space-y-3 rounded-xl bg-indigo-50 p-4 ring-1 ring-indigo-200">
      <h2 className="font-semibold text-stone-900">토큰을 발급했습니다</h2>
      <p className="text-sm text-stone-900">한 번만 표시됩니다. 지금 복사하세요.</p>
      <div><label htmlFor={fieldId} className="block text-sm text-stone-900">토큰 값</label>
        <textarea id={fieldId} ref={field} value={token} readOnly autoComplete="off" spellCheck={false} className={`${inputClass} mt-1 font-mono text-xs`} rows={3} />
      </div>
      <div className="flex flex-wrap gap-4 text-sm">
        <button type="button" className="font-medium text-indigo-700 underline" onClick={async () => {
          try { await navigator.clipboard.writeText(token); setNote("복사했습니다."); }
          catch { field.current?.focus(); field.current?.select(); setNote("복사하지 못했습니다. 선택된 값을 직접 복사하세요."); }
        }}>토큰 복사</button>
        <button type="button" className="text-stone-900 underline" onClick={onClose}>확인했고 닫기</button>
      </div>
      {note && <p role="status" className="text-sm text-stone-900">{note}</p>}
    </section>
  );
}

export default function PatManagement() {
  const formId = useId();
  const { user, csrfToken, refresh } = useAuth();
  const [tick, setTick] = useState(0);
  const list = usePagedList(`pats:${tick}`, listPats);
  const [name, setName] = useState("");
  const [expiryDays, setExpiryDays] = useState("30");
  const [issued, setIssued] = useState<IssuedPat | null>(null);
  // name/expiry/key만 메모리에 둔다. 응답의 token은 요청이나 브라우저 storage에 넣지 않는다.
  const [pending, setPending] = useState<{ key: string; body: PatInput; uncertain: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const alive = useRef(true);
  const actionEpoch = useRef(0);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    alive.current = true;
    const timer = setInterval(() => setNow(Date.now()), 30000);
    // bfcache의 뒤로 가기로도 발급 값을 되살리지 않는다. 이미 보낸 요청의 늦은 응답도 버린다.
    const clearPrivateView = () => {
      actionEpoch.current++;
      busyRef.current = false;
      flushSync(() => { setIssued(null); setPending(null); setBusy(false); });
    };
    window.addEventListener("pagehide", clearPrivateView);
    return () => { alive.current = false; clearInterval(timer); window.removeEventListener("pagehide", clearPrivateView); };
  }, []);
  useEffect(() => {
    if (list.failure?.kind === "http" && list.failure.status === 401) void refresh();
  }, [list.failure, refresh]);
  const reload = () => setTick((n) => n + 1);

  async function submit(attempt: { key: string; body: PatInput; uncertain: boolean }) {
    if (busyRef.current || !csrfToken || !user?.isApproved) return;
    busyRef.current = true;
    const epoch = actionEpoch.current;
    setBusy(true); setFailure(""); setNotice(""); setPending(attempt);
    try {
      const result = await issuePat(attempt.body, csrfToken, attempt.key);
      if (!alive.current || epoch !== actionEpoch.current) return;
      if (result.ok) {
        setPending(null); setIssued(result.data); setName(""); reload();
      } else {
        setFailure(patFailureMessage(result.failure));
        // 현재 429/403/422 등은 앞선 commit 여부를 확정하지 않는다. 한 번 불확실했던 key/body는
        // 성공 응답으로 확인하거나 사용자가 명시적으로 닫기 전까지 메모리에 유지한다.
        if (attempt.uncertain || isUncertain(result.failure)) setPending({ ...attempt, uncertain: true });
        else setPending(null);
        if (result.failure.kind === "http" && (result.failure.status === 401 || result.failure.code === "csrf_invalid")) void refresh();
      }
    } finally {
      if (epoch === actionEpoch.current) { busyRef.current = false; if (alive.current) setBusy(false); }
    }
  }
  function issue(event: FormEvent) {
    event.preventDefault();
    if (pending || issued || busyRef.current) return;
    const days = Number(expiryDays);
    if (!name.trim() || name.trim().length > 80 || !Number.isInteger(days) || days < 1 || days > 90) {
      setFailure("이름은 1–80자, 만료 기간은 1–90일로 입력하세요."); return;
    }
    void submit({ key: uuidV4(), body: { name: name.trim(), expiryDays: days }, uncertain: false });
  }
  async function revoke(id: string) {
    if (busyRef.current || !csrfToken) return;
    busyRef.current = true; setBusy(true); setFailure(""); setNotice("");
    const epoch = actionEpoch.current;
    try {
      const result = await revokePat(id, csrfToken);
      if (!alive.current || epoch !== actionEpoch.current) return;
      if (result.ok) {
        setConfirmRevoke(null); setNotice("토큰을 폐기했습니다.");
        if (issued?.credential.id === id) setIssued(null);
        reload();
      } else {
        setFailure(patFailureMessage(result.failure));
        if (result.failure.kind === "http" && (result.failure.status === 401 || result.failure.code === "csrf_invalid")) void refresh();
      }
    } finally { if (epoch === actionEpoch.current) { busyRef.current = false; if (alive.current) setBusy(false); } }
  }
  const locked = busy || !!pending || !!issued;
  return (
    <div className="space-y-6">
      {!user?.isApproved && <Notice tone="warn">승인 후 발급할 수 있습니다. 기존 토큰은 조회·폐기할 수 있습니다.</Notice>}
      {user?.isApproved && user.role === "commenter" && <Notice tone="info">현재 계정은 공개 콘텐츠만 읽을 수 있습니다.</Notice>}
      <form onSubmit={issue} className="space-y-3 rounded-xl p-4 ring-1 ring-stone-200">
        <h2 className="font-semibold text-stone-900">새 API 토큰 발급</h2>
        <div><label htmlFor={`${formId}-name`} className="block text-sm text-stone-900">이름</label>
          <input id={`${formId}-name`} value={name} onChange={(event) => setName(event.target.value)} maxLength={80} required readOnly={locked} autoComplete="off" className={`${inputClass} mt-1`} />
        </div>
        <div><label htmlFor={`${formId}-expiry`} className="block text-sm text-stone-900">만료 기간(일)</label>
          <input id={`${formId}-expiry`} type="number" min={1} max={90} step={1} value={expiryDays} onChange={(event) => setExpiryDays(event.target.value)} required readOnly={locked} className={`${inputClass} mt-1`} />
        </div>
        <button type="submit" disabled={locked || !user?.isApproved || !csrfToken} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy && pending ? "발급 결과 확인 중..." : "API 토큰 발급"}</button>
      </form>

      {pending && !busy && (
        <Notice tone="warn">
          발급 결과를 확인하세요. 이미 발급된 값은 다시 표시되지 않습니다.
          <div className="mt-2 flex flex-wrap gap-4">
            <button type="button" onClick={() => void submit(pending)} className="font-medium underline">같은 요청 다시 확인</button>
            <button type="button" onClick={() => { setPending(null); setNotice("목록을 확인하세요. 값을 잃은 토큰은 폐기하세요."); reload(); }} className="underline">요청 닫고 목록 확인</button>
          </div>
        </Notice>
      )}
      {issued?.token && <OneTimeValue key={issued.credential.id} token={issued.token} onClose={() => { setIssued(null); setNotice("토큰 값을 닫았습니다."); }} />}
      {issued && !issued.token && (
        <Notice tone="warn">
          ‘{issued.credential.name}’ 값은 복구할 수 없습니다. 폐기 후 다시 발급하세요.
          <div className="mt-2 flex flex-wrap gap-4">
            <button type="button" disabled={busy} onClick={() => void revoke(issued.credential.id)} className="font-medium underline">값을 잃은 토큰 폐기</button>
            <button type="button" disabled={busy} onClick={() => setIssued(null)} className="underline">닫고 목록에서 관리</button>
          </div>
        </Notice>
      )}
      {failure && <p role="alert" className="text-sm text-amber-800">{failure}</p>}
      {notice && <p role="status" className="text-sm text-stone-900">{notice}</p>}

      <section aria-label="API 토큰 목록" className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-semibold text-stone-900">내 API 토큰</h2>
          <button type="button" disabled={busy} onClick={reload} className="text-sm text-indigo-600 underline">목록 새로 불러오기</button>
        </div>
        {list.failure && <p role="alert" className="text-sm text-amber-800">목록을 불러오지 못했습니다. {patFailureMessage(list.failure)}</p>}
        {list.loading && <p role="status" className="text-sm text-stone-500">토큰 목록을 불러오는 중...</p>}
        {list.loaded && !list.loading && !list.failure && list.items.length === 0 && <p className="text-sm text-stone-500">발급한 API 토큰이 없습니다.</p>}
        <ul className="space-y-3">
          {list.items.map((pat) => {
            const state = patStatus(pat, now);
            return (
              <li key={pat.id} className="space-y-2 rounded-xl p-4 text-sm ring-1 ring-stone-200">
                <div className="flex flex-wrap items-baseline justify-between gap-2"><span className="break-all font-medium text-stone-900">{pat.name}</span><span className="text-xs text-stone-500">{state === "revoked" ? "폐기됨" : state === "expired" ? "만료됨" : "활성"}</span></div>
                <p className="text-xs text-stone-500">발급 {formatDateTime(pat.issuedAt)} · 만료 {formatDateTime(pat.expiresAt)}</p>
                <p className="text-xs text-stone-500">마지막 사용: {pat.lastUsedAt ? formatDateTime(pat.lastUsedAt) : "아직 사용하지 않음"}</p>
                {pat.revokedAt && <p className="text-xs text-stone-500">폐기 {formatDateTime(pat.revokedAt)}</p>}
                {!pat.revokedAt && (confirmRevoke === pat.id ? (
                  <div className="space-y-2"><p>‘{pat.name}’ 토큰을 폐기할까요?</p><div className="flex gap-4"><button type="button" disabled={busy} onClick={() => void revoke(pat.id)} className="font-medium text-red-700 underline">폐기 확인</button><button type="button" disabled={busy} onClick={() => setConfirmRevoke(null)} className="text-stone-900 underline">취소</button></div></div>
                ) : <button type="button" disabled={busy} onClick={() => setConfirmRevoke(pat.id)} className="text-red-700 underline">토큰 폐기</button>)}
              </li>
            );
          })}
        </ul>
        {list.nextCursor && <button type="button" disabled={list.loading || busy} onClick={() => void list.loadMore()} className="text-sm text-indigo-600 underline">이전 토큰 더 보기</button>}
      </section>
    </div>
  );
}
