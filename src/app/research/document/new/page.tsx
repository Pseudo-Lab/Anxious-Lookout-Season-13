"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { createDocument, type DocumentInput } from "@/lib/research/api";
import { useMutation, useRestoredRequest } from "@/lib/research/hooks";
import Notice from "@/components/research/Notice";
import AuthGate from "@/components/research/AuthGate";
import { DocumentForm } from "@/components/research/ItemForms";

// 결과 미확인 생성 요청의 계정별 보관 범위
const SCOPE = "create:document";

function NewDocument() {
  const router = useRouter();
  const restored = useRestoredRequest<DocumentInput>(SCOPE);
  const { run, busy } = useMutation(SCOPE);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function submit(input: DocumentInput) {
    const res = await run(`document:create:${JSON.stringify(input)}`, (ctx) => createDocument(input, ctx), input);
    if (res.ok) router.push(`/research/document/?id=${encodeURIComponent(res.data.id)}`);
    else setFailure(res.failure);
  }

  return (
    <div className="space-y-4">
      {restored && (
        <Notice tone="warn">
          이전 저장 요청의 결과를 확인하지 못했습니다. 아래 내용을 그대로 다시 저장하면 이미 저장된 경우에도 중복으로
          만들어지지 않습니다. 내용을 바꾸면 새 요청이 됩니다.
        </Notice>
      )}
      <DocumentForm
        initial={restored ?? undefined}
        submitLabel="문서 저장"
        busy={busy}
        failure={failure}
        onSubmit={(i) => void submit(i)}
      />
    </div>
  );
}

export default function NewDocumentPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <Link href="/research/?tab=documents" className="text-sm text-stone-500 hover:text-stone-800">
        ← 문서 목록
      </Link>
      <h1 className="mb-6 mt-2 text-2xl font-bold text-stone-900">새 문서</h1>
      <AuthGate>
        <NewDocument />
      </AuthGate>
    </div>
  );
}
