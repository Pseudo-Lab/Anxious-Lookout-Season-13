"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import type { JSONContent } from "@tiptap/react";
import AuthGate from "@/components/research/AuthGate";

// #32 spike: 고정 블록 편집기 시험 화면(저장하지 않는다). 편집기 안에서 만든 구조를 그대로 확인하려고 내부 JSON을 보여 준다.
// adapter(post-blocks 변환)와 글 편집 화면 연결은 back의 JSON Schema·golden fixture가 나온 뒤에 한다. 그때 이 화면은 없앤다.

const BlockEditor = dynamic(() => import("@/components/blocks/editor/BlockEditor"), {
  ssr: false,
  loading: () => <p className="text-sm text-stone-500">편집기를 불러오는 중...</p>,
});

export default function BlocksLabPage() {
  const [doc, setDoc] = useState<JSONContent | null>(null);
  const [uploading, setUploading] = useState(false);
  return (
    <div className="mx-auto max-w-4xl">
      <Link href="/write/" className="text-sm text-stone-500 hover:text-stone-800">
        ← 내 글
      </Link>
      <h1 className="mb-2 mt-2 text-2xl font-bold text-stone-900">블록 편집기 시험</h1>
      <p className="mb-4 text-sm text-stone-600">#32 편집기 검증용 화면입니다. 여기서 쓴 내용은 저장되지 않습니다(이미지는 실제로 올라갑니다).</p>
      <AuthGate>
        <BlockEditor onChange={setDoc} onUploading={setUploading} />
        <p className="mt-2 text-xs text-stone-500" role="status" aria-label="업로드 상태">
          {uploading ? "이미지를 올리는 중" : "대기"}
        </p>
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-stone-600">편집기 내부 JSON</summary>
          <pre aria-label="편집기 JSON" className="mt-2 max-h-96 overflow-auto rounded bg-stone-900 p-3 text-xs text-stone-100">
            {doc ? JSON.stringify(doc, null, 2) : ""}
          </pre>
        </details>
      </AuthGate>
    </div>
  );
}
