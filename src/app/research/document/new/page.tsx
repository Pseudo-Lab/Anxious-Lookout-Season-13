"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { getDraft, listDrafts } from "@/lib/research/api";
import { usePagedList } from "@/lib/research/usePagedList";
import { formatDateTime } from "@/lib/research/format";
import type { Draft } from "@/lib/research/types";
import AuthGate from "@/components/research/AuthGate";
import DocumentEditor from "@/components/research/DocumentEditor";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";

// #22 새 문서: 입력은 서버 초안(documentId 없음)으로 자동 저장되고 주소(?draft=)로 복구된다. ‘문서 저장’만 문서와 첫 버전을 만든다.
// 초안은 계정별 서버 자원이라 다른 계정에는 보이지 않는다(브라우저 저장소에 본문을 두지 않는다).

const NEW_PATH = "/research/document/new/";

// 이어 쓸 수 있는 다른 새 문서 초안. 목록 API는 기존 문서 초안도 함께 주므로 걸러 내고, 다음 페이지도 이어서 찾는다.
// reload: 편집기를 새로 시작할 때(버리기 등)마다 바뀌어 목록을 다시 불러온다.
function OtherDrafts({ current, reload }: { current: string | null; reload: string }) {
  const list = usePagedList(`new-document-drafts:${reload}`, (cursor) => listDrafts(cursor));
  const drafts = list.items.filter((d) => d.documentId === null && d.id !== current);
  // 지금까지 받은 페이지에 보일 새 문서 초안이 없으면 남은 페이지를 이어서 찾는다(앞 페이지가 기존 문서 초안뿐인 경우).
  const { loaded, loading, nextCursor, loadMore } = list;
  const searching = loaded && drafts.length === 0 && !!nextCursor && !list.failure;
  useEffect(() => {
    if (searching && !loading) void loadMore();
  }, [searching, loading, loadMore]);
  if (!drafts.length && !searching && !list.failure) return null;
  return (
    <section className="rounded-lg p-3 ring-1 ring-stone-200">
      <h2 className="mb-2 text-sm font-semibold text-stone-700">작성 중인 새 문서 초안</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="초안 목록을 불러오지 못했습니다." />}
      <ul className="space-y-1 text-sm">
        {drafts.map((d) => (
          <li key={d.id}>
            <Link href={`${NEW_PATH}?draft=${encodeURIComponent(d.id)}`} className="text-indigo-600 hover:text-indigo-800">
              {d.title || "(제목 없음)"}
            </Link>
            <span className="ml-2 text-xs text-stone-500">자동 저장 {formatDateTime(d.updatedAt)}</span>
          </li>
        ))}
      </ul>
      {searching && <p className="text-xs text-stone-500">초안을 찾는 중...</p>}
      {!searching && nextCursor && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loading}
          className="mt-2 text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
        >
          초안 더 보기
        </button>
      )}
    </section>
  );
}

// 지금 화면의 편집기와 그 편집기가 쓰는 초안.
// bound: 편집기가 저장하는 초안 ID(새 편집기는 첫 자동 저장 전까지 null).
// transit: 주소가 아직 따라오지 않은 이전 주소 값(편집기가 방금 만든 초안으로 바꾸는 중이거나, 찾지 못한 초안 주소에서 새로 시작한 경우).
// 주소(?draft=)가 bound·transit과 맞지 않는 동안에는 편집기를 그리지 않는다(다른 초안 주소에서 이 초안을 고치지 않게, 리뷰 F4).
interface View {
  key: string;
  draft: Draft | null;
  bound: string | null;
  transit?: string | null;
  note?: string;
}
const matches = (v: View, param: string | null) => param === v.bound || (v.transit !== undefined && param === v.transit);

function NewDocument() {
  const router = useRouter();
  const param = useSearchParams().get("draft");
  const [view, setView] = useState<View | null>(param ? null : { key: "new", draft: null, bound: null });
  const viewRef = useRef(view);
  const paramRef = useRef(param);
  useEffect(() => {
    viewRef.current = view;
    paramRef.current = param;
  });

  useEffect(() => {
    const v = viewRef.current;
    let active = true;
    if (v && param === v.bound) {
      // 주소가 지금 편집기를 따라왔다. 이전 주소 값은 더 맞추지 않는다.
      if (v.transit !== undefined) void Promise.resolve().then(() => active && setView((cur) => (cur && cur.key === v.key ? { ...cur, transit: undefined } : cur)));
      return () => {
        active = false;
      };
    }
    if (v && v.transit !== undefined && param === v.transit) return;
    if (!param) {
      // 다른 초안을 보다가 새 문서로 왔다
      void Promise.resolve().then(() => active && setView({ key: `new:${Date.now()}`, draft: null, bound: null }));
      return () => {
        active = false;
      };
    }
    void getDraft(param).then((r) => {
      if (!active) return;
      if (r.ok && r.data.state === "active" && r.data.documentId === null) setView({ key: r.data.id, draft: r.data, bound: r.data.id });
      else if (r.ok && r.data.state === "committed" && r.data.documentId)
        router.replace(`/research/document/?id=${encodeURIComponent(r.data.documentId)}`);
      else
        setView({ key: `new:${param}:${Date.now()}`, draft: null, bound: null, transit: param, note: "이어 쓸 초안을 찾지 못해 새 문서로 시작합니다." });
    });
    return () => {
      active = false;
    };
  }, [param, router]);

  if (!view || !matches(view, param)) return <p className="text-sm text-stone-500">초안을 불러오는 중...</p>;
  const key = view.key;
  return (
    <div className="space-y-4">
      {view.note && <Notice tone="info">{view.note}</Notice>}
      <DocumentEditor
        key={key}
        doc={null}
        draft={view.draft}
        onDraft={(d) => {
          // 이미 다른 편집기로 바뀌었으면(늦은 자동 저장 응답) 주소를 건드리지 않는다.
          const cur = viewRef.current;
          if (!cur || cur.key !== key || cur.bound === d.id) return;
          const from = paramRef.current;
          setView((v) => (v && v.key === key ? { ...v, bound: d.id, transit: from } : v));
          router.replace(`${NEW_PATH}?draft=${encodeURIComponent(d.id)}`, { scroll: false });
        }}
        onCommitted={(doc) => router.push(`/research/document/?id=${encodeURIComponent(doc.id)}`)}
        onDiscarded={() => {
          setView({ key: `new:${Date.now()}`, draft: null, bound: null, transit: paramRef.current, note: "초안을 버렸습니다." });
          router.replace(NEW_PATH, { scroll: false });
        }}
      />
      <OtherDrafts current={view.bound} reload={key} />
    </div>
  );
}

export default function NewDocumentPage() {
  return (
    // 편집기 나란히 보기를 위해 넓게 쓴다.
    <div className="mx-auto max-w-6xl">
      <Link href="/research/?tab=documents" className="text-sm text-stone-500 hover:text-stone-800">
        ← 문서 목록
      </Link>
      <h1 className="mb-6 mt-2 text-2xl font-bold text-stone-900">새 문서</h1>
      <AuthGate>
        <Suspense>
          <NewDocument />
        </Suspense>
      </AuthGate>
    </div>
  );
}
