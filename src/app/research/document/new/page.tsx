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
import Notice from "@/components/research/Notice";

// #22 새 문서: 입력은 서버 초안(documentId 없음)으로 자동 저장되고 주소(?draft=)로 복구된다. ‘문서 저장’만 문서와 첫 버전을 만든다.
// 초안은 계정별 서버 자원이라 다른 계정에는 보이지 않는다(브라우저 저장소에 본문을 두지 않는다).

const NEW_PATH = "/research/document/new/";

// 이어 쓸 수 있는 다른 새 문서 초안
// reload: 편집기를 새로 시작할 때(버리기 등)마다 바뀌어 목록을 다시 불러온다.
function OtherDrafts({ current, reload }: { current: string | null; reload: string }) {
  const list = usePagedList(`new-document-drafts:${reload}`, (cursor) => listDrafts(cursor));
  const drafts = list.items.filter((d) => d.documentId === null && d.id !== current);
  if (!drafts.length) return null;
  return (
    <section className="rounded-lg p-3 ring-1 ring-stone-200">
      <h2 className="mb-2 text-sm font-semibold text-stone-700">작성 중인 새 문서 초안</h2>
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
    </section>
  );
}

function NewDocument() {
  const router = useRouter();
  const draftParam = useSearchParams().get("draft");
  // 이 화면에서 방금 만든 초안이면 주소가 바뀌어도 다시 불러오지 않는다(입력 중인 편집기를 다시 그리지 않게).
  const own = useRef<string | null>(null);
  // 화면에 쓰는 같은 값(목록에서 지금 초안을 빼는 데 쓴다)
  const [created, setCreated] = useState<string | null>(null);
  const [state, setState] = useState<{ key: string; draft: Draft | null; note?: string } | null>(
    draftParam ? null : { key: "new", draft: null }
  );

  useEffect(() => {
    if (!draftParam || draftParam === own.current) return;
    let active = true;
    void getDraft(draftParam).then((r) => {
      if (!active) return;
      if (r.ok && r.data.state === "active" && r.data.documentId === null) setState({ key: r.data.id, draft: r.data });
      else if (r.ok && r.data.state === "committed" && r.data.documentId)
        router.replace(`/research/document/?id=${encodeURIComponent(r.data.documentId)}`);
      else setState({ key: `new:${draftParam}`, draft: null, note: "이어 쓸 초안을 찾지 못해 새 문서로 시작합니다." });
    });
    return () => {
      active = false;
    };
  }, [draftParam, router]);

  if (!state) return <p className="text-sm text-stone-500">초안을 불러오는 중...</p>;
  return (
    <div className="space-y-4">
      {state.note && <Notice tone="info">{state.note}</Notice>}
      <DocumentEditor
        key={state.key}
        doc={null}
        draft={state.draft}
        onDraft={(d) => {
          if (own.current === d.id || d.id === draftParam) return;
          own.current = d.id;
          setCreated(d.id);
          router.replace(`${NEW_PATH}?draft=${encodeURIComponent(d.id)}`, { scroll: false });
        }}
        onCommitted={(doc) => router.push(`/research/document/?id=${encodeURIComponent(doc.id)}`)}
        onDiscarded={() => {
          own.current = null;
          setCreated(null);
          setState({ key: `new:${Date.now()}`, draft: null, note: "초안을 버렸습니다." });
          router.replace(NEW_PATH, { scroll: false });
        }}
      />
      <OtherDrafts current={state.draft?.id ?? created} reload={state.key} />
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
