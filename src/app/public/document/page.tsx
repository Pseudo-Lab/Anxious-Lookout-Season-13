"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { API_ENABLED } from "@/lib/constants";
import { useAuth } from "@/hooks/useAuth";
import { GITHUB_LOGIN_URL, canUseResearch } from "@/lib/auth/api";
import { getPublicDocument } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";
import { CONTENT_KIND_LABELS, type PublicSnapshot } from "@/lib/research/types";
import { formatDateTime } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import NewSessionForm from "@/components/research/NewSessionForm";
import SafeMarkdown, { safeHref } from "@/components/research/SafeMarkdown";

// 이 문서를 맥락으로 개인 대화를 시작한다. 대화는 방문자 본인에게만 보이며 작성자는 볼 수 없다.
// 공개 열람은 누구나 가능하지만 질문(모델 사용)은 승인된 편집자·관리자만 할 수 있다(최종 판단은 서버).
function StartConversation({ doc }: { doc: PublicSnapshot }) {
  const { status, user } = useAuth();
  const allowed = canUseResearch(user);
  return (
    <section className="space-y-3 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-stone-200/60">
      <h2 className="font-semibold text-stone-800">이 문서로 Codex와 대화하기</h2>
      <p className="text-xs text-stone-500">
        대화는 내 개인 기록으로 저장되며 문서 작성자를 포함한 다른 사람은 볼 수 없습니다. 대화 중에 내 다른 자료도 활용할 수 있습니다.
      </p>
      {status === "authenticated" && user && !allowed ? (
        <p className="text-sm text-stone-600">
          이 글에 대한 Codex 질문은 관리자가 승인한 편집자·관리자만 이용할 수 있습니다. 글과 참고 자료는 누구나 읽을 수 있습니다.
        </p>
      ) : status === "authenticated" && user ? (
        // 개인 입력(복구된 제목 포함)은 계정에 묶는다. 계정이 바뀌면 폼을 새로 그려 이전 계정 입력을 버린다.
        <NewSessionForm key={user.accountId} publicDocumentId={doc.documentId} defaultTitle={doc.title.slice(0, 280)} />
      ) : status === "unauthenticated" ? (
        <p className="text-sm text-stone-600">
          <a href={GITHUB_LOGIN_URL} className="font-medium text-indigo-600 hover:text-indigo-800">
            GitHub로 로그인하고 대화 시작
          </a>{" "}
          <span className="text-xs text-stone-500">(승인된 편집자·관리자만 질문할 수 있습니다)</span>
        </p>
      ) : null}
    </section>
  );
}

function MaterialCard({ m }: { m: PublicSnapshot["materials"][number] }) {
  const source = safeHref(m.sourceUrl);
  return (
    <li className="space-y-2 rounded-lg bg-white p-4 ring-1 ring-stone-200">
      <p className="font-medium text-stone-900">{m.title || "(제목 없음)"}</p>
      <p className="break-all text-xs text-stone-500">
        {source ? (
          <a href={source} target="_blank" rel="noopener noreferrer nofollow ugc" className="text-indigo-600 hover:text-indigo-800">
            {m.sourceUrl}
          </a>
        ) : (
          m.sourceUrl
        )}{" "}
        · {CONTENT_KIND_LABELS[m.contentKind]} · 수집 {formatDateTime(m.collectedAt)}
      </p>
      {m.content !== null && (
        <details>
          <summary className="cursor-pointer text-xs text-stone-500">저장된 내용 보기</summary>
          <div className="mt-2">
            <SafeMarkdown content={m.content} />
          </div>
        </details>
      )}
    </li>
  );
}

function PublicDocument({ id }: { id: string }) {
  const { result } = useLoad(`public:${id}`, () => getPublicDocument(id));
  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) {
    // 공개 문서 조회 실패(철회·없음 포함). 개인 세션 실패와 구분해 안내한다.
    if (result.failure.kind === "http" && result.failure.code === "not_found") {
      return <Notice tone="info">공개되지 않았거나 공개가 철회된 문서입니다.</Notice>;
    }
    return <ErrorNotice failure={result.failure} prefix="공개 문서를 불러오지 못했습니다." />;
  }
  const d = result.data;
  return (
    <div className="space-y-10">
      <article className="space-y-4">
        <header>
          <h1 className="text-2xl font-bold text-stone-900">{d.title || "(제목 없음)"}</h1>
          <p className="mt-1 text-sm text-stone-500">
            {d.author.login} · {formatDateTime(d.publishedAt)} 공개
          </p>
        </header>
        <SafeMarkdown content={d.content} />
      </article>
      {d.materials.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold text-stone-800">참고 자료</h2>
          <ul className="space-y-3">
            {d.materials.map((m) => (
              <MaterialCard key={m.versionId} m={m} />
            ))}
          </ul>
        </section>
      )}
      <StartConversation doc={d} />
    </div>
  );
}

function Inner() {
  const id = useSearchParams().get("id");
  if (!id) return <Notice tone="error">문서 ID가 없습니다.</Notice>;
  return <PublicDocument key={id} id={id} />;
}

export default function PublicDocumentPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <Link href="/public/" className="mb-4 inline-block text-sm text-stone-500 hover:text-stone-800">
        ← 공개 문서
      </Link>
      {API_ENABLED ? (
        <Suspense>
          <Inner />
        </Suspense>
      ) : (
        <Notice tone="info">이 정적 사이트에는 API 서버가 없어 공개 문서를 제공하지 않습니다.</Notice>
      )}
    </div>
  );
}
