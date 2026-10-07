"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { createMaterial, type MaterialInput } from "@/lib/research/api";
import { useMutation, useRestoredRequest } from "@/lib/research/hooks";
import Notice from "@/components/research/Notice";
import AuthGate from "@/components/research/AuthGate";
import { MaterialForm } from "@/components/research/ItemForms";

// 결과 미확인 생성 요청의 계정별 보관 범위
const SCOPE = "create:material";

function NewMaterial() {
  const router = useRouter();
  const restored = useRestoredRequest<MaterialInput>(SCOPE);
  const { run, busy } = useMutation(SCOPE);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function submit(input: MaterialInput) {
    const res = await run(`material:create:${JSON.stringify(input)}`, (ctx) => createMaterial(input, ctx), input);
    if (res.ok) router.push(`/research/material/?id=${encodeURIComponent(res.data.id)}`);
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
      <MaterialForm
        initial={restored ?? undefined}
        submitLabel="자료 저장"
        busy={busy}
        failure={failure}
        onSubmit={(i) => void submit(i)}
      />
    </div>
  );
}

export default function NewMaterialPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <Link href="/research/?tab=materials" className="text-sm text-stone-500 hover:text-stone-800">
        ← 자료 목록
      </Link>
      <h1 className="mb-6 mt-2 text-2xl font-bold text-stone-900">새 자료</h1>
      <AuthGate>
        <NewMaterial />
      </AuthGate>
    </div>
  );
}
