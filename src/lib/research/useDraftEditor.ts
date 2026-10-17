"use client";

// #22 서버 초안 자동 저장(api-contracts/issue22-writing-editor.md). 초안은 버전이 아니며 명시 저장(commit)만 버전을 만든다.
// - 입력이 멈추고 AUTOSAVE_IDLE_MS 뒤, 길어도 AUTOSAVE_MAX_MS 안에 저장한다. 요청은 한 번에 하나이고 응답 revision으로 다음을 보낸다.
// - 결과를 모르는 요청(네트워크·timeout·5xx)은 같은 내용·같은 key로 먼저 다시 보낸 뒤 이후 입력을 보낸다(중복 초안·자기 충돌 방지).
// - 다른 탭·기기 변경(409 draft_revision / active_draft_exists)은 조용히 덮어쓰지 않고 사용자가 고르게 한다.

import { useCallback, useEffect, useRef, useState } from "react";
import type { FetchFailure, FetchResult } from "@/lib/api/client";
import { createDraft, getDocumentDraft, getDraft, saveDraft, type DraftInput } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { isUncertain } from "@/lib/research/idempotency";
import type { Draft } from "@/lib/research/types";

export const AUTOSAVE_IDLE_MS = 2000;
export const AUTOSAVE_MAX_MS = 10000;
const RETRY_MS = [3000, 10000, 30000];

export type SaveStatus =
  // 서버(또는 원본 문서)와 같음
  | { kind: "clean" }
  // 바뀌었고 곧 저장한다
  | { kind: "dirty" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  // 일시 실패. 같은 요청으로 자동 재시도한다
  | { kind: "retrying"; failure: FetchFailure }
  // 확정 실패(권한·보관·문서 변경·닫힌 초안 등). 자동 저장을 멈춘다
  | { kind: "stopped"; failure: FetchFailure }
  // 다른 탭·기기에서 바뀐 서버 초안. server가 null이면 다시 읽지 못했다
  | { kind: "conflict"; failure: FetchFailure; server: Draft | null };

export interface DraftBase {
  documentId: string;
  baseDocumentVersion: number;
  baseVersionId: string;
}

const same = (a: DraftInput | null, b: DraftInput) => !!a && a.title === b.title && a.content === b.content;

// 충돌한 서버 초안을 다시 읽는다(비교·선택용). 읽지 못하면 null.
async function serverDraft(reason: string | undefined, draft: Draft | null, base: DraftBase | null): Promise<Draft | null> {
  if (reason === "draft_revision" && draft) {
    const r = await getDraft(draft.id);
    return r.ok ? r.data : null;
  }
  if (base) {
    const r = await getDocumentDraft(base.documentId);
    return r.ok ? r.data.draft : null;
  }
  return null;
}

export function useDraftEditor({
  initial,
  draft: initialDraft,
  base,
  onDraft,
}: {
  // 초안이 없을 때의 시작 내용(기존 문서 본문 또는 빈 새 문서)
  initial: DraftInput;
  // 이어 쓸 active 초안
  draft: Draft | null;
  // 기존 문서면 초안을 만들 때의 base. 새 문서면 null
  base: DraftBase | null;
  // 서버 초안이 만들어지거나 바뀔 때마다
  onDraft?: (draft: Draft) => void;
}) {
  const start: DraftInput = initialDraft ? { title: initialDraft.title, content: initialDraft.content ?? "" } : initial;
  const [values, setValuesState] = useState<DraftInput>(start);
  const [draft, setDraftState] = useState<Draft | null>(initialDraft);
  const [status, setStatusState] = useState<SaveStatus>(initialDraft ? { kind: "saved", at: initialDraft.updatedAt } : { kind: "clean" });
  const { run } = useMutation();

  const valuesRef = useRef(start);
  const draftRef = useRef(initialDraft);
  // 서버에 있는 것으로 확인된 내용(초안이 없으면 시작 내용)
  const savedRef = useRef<DraftInput>(start);
  // 결과를 모르는 요청의 내용. 다음 저장은 이것부터 같은 key로 다시 보낸다
  const pendingRef = useRef<DraftInput | null>(null);
  const statusRef = useRef<SaveStatus>(status);
  const baseRef = useRef(base);
  const onDraftRef = useRef(onDraft);
  const runRef = useRef(run);
  const inflight = useRef<Promise<void> | null>(null);
  const again = useRef(false);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retries = useRef(0);
  const alive = useRef(true);
  const saveRef = useRef<() => Promise<void>>(() => Promise.resolve());

  useEffect(() => {
    baseRef.current = base;
    onDraftRef.current = onDraft;
    runRef.current = run;
  });

  const setStatus = useCallback((s: SaveStatus) => {
    statusRef.current = s;
    if (alive.current) setStatusState(s);
  }, []);
  const setDraft = useCallback((d: Draft | null) => {
    draftRef.current = d;
    if (alive.current) setDraftState(d);
    // 닫힌(언마운트된) 편집기의 늦은 저장 결과는 화면에 알리지 않는다(다른 편집기의 주소를 되돌리지 않게).
    if (d && alive.current) onDraftRef.current?.(d);
  }, []);

  const clearTimers = useCallback(() => {
    for (const t of [idleTimer, maxTimer, retryTimer]) {
      if (t.current) clearTimeout(t.current);
      t.current = null;
    }
  }, []);

  const blocked = () => statusRef.current.kind === "stopped" || statusRef.current.kind === "conflict";

  const saveOnce = useCallback(async (): Promise<void> => {
    const pending = pendingRef.current;
    const body = pending ?? valuesRef.current;
    if (!pending && same(savedRef.current, body)) {
      setStatus(draftRef.current ? { kind: "saved", at: draftRef.current.updatedAt } : { kind: "clean" });
      return;
    }
    setStatus({ kind: "saving" });
    const d = draftRef.current;
    const b = baseRef.current;
    const text = `${body.title}\u0000${body.content}`;
    let res: FetchResult<Draft>;
    if (d) {
      res = await runRef.current(`draft:save:${d.id}:${d.revision}:${text}`, (ctx) => saveDraft(d.id, body, d.revision, ctx));
    } else {
      res = await runRef.current(`draft:create:${b?.documentId ?? "new"}:${text}`, (ctx) =>
        createDraft(b ? { ...body, ...b } : body, ctx)
      );
    }
    if (res.ok) {
      pendingRef.current = null;
      retries.current = 0;
      savedRef.current = body;
      setDraft(res.data);
      if (same(body, valuesRef.current)) setStatus({ kind: "saved", at: res.data.updatedAt });
      else again.current = true;
      return;
    }
    const failure = res.failure;
    if (isUncertain(failure)) {
      pendingRef.current = body;
      setStatus({ kind: "retrying", failure });
      const wait = RETRY_MS[Math.min(retries.current++, RETRY_MS.length - 1)];
      retryTimer.current = setTimeout(() => void saveRef.current(), wait);
      return;
    }
    pendingRef.current = null;
    if (failure.kind === "http" && failure.code === "conflict" && (failure.reason === "draft_revision" || failure.reason === "active_draft_exists")) {
      setStatus({ kind: "conflict", failure, server: await serverDraft(failure.reason, d, b) });
      return;
    }
    setStatus({ kind: "stopped", failure });
  }, [setDraft, setStatus]);

  // 한 번에 하나만 보낸다. 진행 중에 바뀌면 끝난 뒤 이어서 보낸다.
  const save = useCallback((): Promise<void> => {
    if (blocked()) return Promise.resolve();
    if (inflight.current) {
      again.current = true;
      return inflight.current;
    }
    clearTimers();
    const p = (async () => {
      do {
        again.current = false;
        await saveOnce();
      } while (again.current && !blocked() && statusRef.current.kind !== "retrying");
    })().finally(() => {
      inflight.current = null;
    });
    inflight.current = p;
    return p;
  }, [clearTimers, saveOnce]);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);

  const schedule = useCallback(() => {
    if (blocked() || statusRef.current.kind === "retrying") return;
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => void save(), AUTOSAVE_IDLE_MS);
    if (!maxTimer.current) maxTimer.current = setTimeout(() => void save(), AUTOSAVE_MAX_MS);
  }, [save]);

  const setValues = useCallback(
    (next: DraftInput) => {
      valuesRef.current = next;
      setValuesState(next);
      if (blocked()) return;
      if (same(savedRef.current, next) && !pendingRef.current) {
        if (idleTimer.current) clearTimeout(idleTimer.current);
        if (maxTimer.current) clearTimeout(maxTimer.current);
        idleTimer.current = maxTimer.current = null;
        if (statusRef.current.kind !== "saving" && statusRef.current.kind !== "retrying")
          setStatus(draftRef.current ? { kind: "saved", at: draftRef.current.updatedAt } : { kind: "clean" });
        return;
      }
      if (statusRef.current.kind !== "saving" && statusRef.current.kind !== "retrying") setStatus({ kind: "dirty" });
      schedule();
    },
    [schedule, setStatus]
  );

  /** 남은 입력을 바로 저장하고 결과를 기다린다. 저장된 초안(없으면 null)과 성공 여부를 돌려준다. */
  const flush = useCallback(async (): Promise<{ ok: boolean; draft: Draft | null }> => {
    if (retryTimer.current) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    if (statusRef.current.kind === "retrying") setStatus({ kind: "dirty" });
    for (let i = 0; i < 3; i++) {
      await save();
      if (inflight.current) await inflight.current;
      if (!pendingRef.current && same(savedRef.current, valuesRef.current)) return { ok: true, draft: draftRef.current };
      if (blocked() || statusRef.current.kind === "retrying") break;
    }
    return { ok: false, draft: draftRef.current };
  }, [save, setStatus]);

  /** 충돌: 서버 초안을 받아들인다(내 입력은 버린다). */
  const takeServer = useCallback(
    (server: Draft) => {
      const v = { title: server.title, content: server.content ?? "" };
      valuesRef.current = v;
      setValuesState(v);
      savedRef.current = v;
      pendingRef.current = null;
      setDraft(server);
      setStatus({ kind: "saved", at: server.updatedAt });
    },
    [setDraft, setStatus]
  );

  /** 충돌: 서버 초안 위에 지금 입력을 저장한다(서버 내용은 그 초안의 이전 상태로만 남는다). */
  const keepMine = useCallback(
    (server: Draft) => {
      savedRef.current = { title: server.title, content: server.content ?? "" };
      pendingRef.current = null;
      setDraft(server);
      setStatus({ kind: "dirty" });
      void save();
    },
    [save, setDraft, setStatus]
  );

  /** 멈춘 자동 저장을 다시 시작한다(문서를 다시 불러온 뒤 등). draft를 주면 그 초안으로 바꾼다. */
  const resume = useCallback(
    (next?: { draft: Draft | null }) => {
      if (next) {
        draftRef.current = next.draft;
        setDraftState(next.draft);
        savedRef.current = next.draft ? { title: next.draft.title, content: next.draft.content ?? "" } : savedRef.current;
      }
      setStatus({ kind: "dirty" });
      void save();
    },
    [save, setStatus]
  );

  // 저장되지 않은 입력이 있으면 떠나기 전에 확인한다. 화면을 닫을 때(언마운트)는 남은 입력을 보낸다.
  useEffect(() => {
    alive.current = true;
    const warn = (e: BeforeUnloadEvent) => {
      const k = statusRef.current.kind;
      if (k === "dirty" || k === "saving" || k === "retrying" || k === "conflict" || k === "stopped") {
        if (!same(savedRef.current, valuesRef.current) || pendingRef.current) e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      alive.current = false;
      clearTimers();
      if (!blocked() && (!same(savedRef.current, valuesRef.current) || pendingRef.current)) void saveRef.current();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { values, setValues, draft, status, flush, takeServer, keepMine, resume };
}
