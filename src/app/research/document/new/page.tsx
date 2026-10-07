"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { createDocument, type DocumentInput } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import AuthGate from "@/components/research/AuthGate";
import { DocumentForm } from "@/components/research/ItemForms";

function NewDocument() {
  const router = useRouter();
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function submit(input: DocumentInput) {
    const res = await run(`document:create:${JSON.stringify(input)}`, (ctx) => createDocument(input, ctx));
    if (res.ok) router.push(`/research/document/?id=${encodeURIComponent(res.data.id)}`);
    else setFailure(res.failure);
  }

  return <DocumentForm submitLabel="문서 저장" busy={busy} failure={failure} onSubmit={(i) => void submit(i)} />;
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
