"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { API_ENABLED } from "@/lib/constants";
import { writePostPath } from "@/lib/routes";
import AuthGate from "@/components/research/AuthGate";
import PostIndex from "./PostIndex";
import WriteFrame from "./WriteFrame";

// #28 글 쓰기 목록(/write/). 새 글은 /write/new/(?draft=), 글 화면은 /write/<id>/(#119 B2).
// 예전 글 화면 주소(/write/?id=<id>)는 새 주소로 넘긴다(#조각 유지).
function WriteInner() {
  const id = useSearchParams().get("id");
  const router = useRouter();
  useEffect(() => {
    if (id && API_ENABLED) router.replace(`${writePostPath(id)}${window.location.hash}`);
  }, [id, router]);
  if (id && API_ENABLED) {
    return (
      <p role="status" className="text-sm text-stone-500">
        새 주소로 이동하는 중...
      </p>
    );
  }
  return <PostIndex />;
}

export default function WritePage() {
  return (
    <WriteFrame>
      <AuthGate>
        <Suspense>
          <WriteInner />
        </Suspense>
      </AuthGate>
    </WriteFrame>
  );
}
