"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import AuthGate from "@/components/research/AuthGate";
import PostDetail from "./PostDetail";
import PostIndex from "./PostIndex";

// #28 글 쓰기. 목록(/write/)과 글 화면(/write/?id=)은 같은 경로이고, 새 글은 /write/new/(?draft=)에서 쓴다.
function WriteInner() {
  const id = useSearchParams().get("id");
  if (id) return <PostDetail key={id} id={id} />;
  return <PostIndex />;
}

export default function WritePage() {
  return (
    // 편집 중에는 나란히 보기를 위해 페이지 폭을 넓힌다(data-editing).
    <div className="mx-auto max-w-3xl has-[[data-editing]]:max-w-6xl">
      <AuthGate>
        <Suspense>
          <WriteInner />
        </Suspense>
      </AuthGate>
      <p className="mt-10 text-xs text-stone-500">
        글은 <Link href="/research/" className="underline">내 연구</Link>의 문서를 바탕으로 씁니다. 자료 → 문서 → 글 순서로 정리하고, 어느 단계에서든 대화창의 Codex에게 도움을 받을 수 있습니다.
      </p>
    </div>
  );
}
