"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { createMaterial, type MaterialInput } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import AuthGate from "@/components/research/AuthGate";
import { MaterialForm } from "@/components/research/ItemForms";

function NewMaterial() {
  const router = useRouter();
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function submit(input: MaterialInput) {
    const res = await run(`material:create:${JSON.stringify(input)}`, (ctx) => createMaterial(input, ctx));
    if (res.ok) router.push(`/research/material/?id=${encodeURIComponent(res.data.id)}`);
    else setFailure(res.failure);
  }

  return <MaterialForm submitLabel="자료 저장" busy={busy} failure={failure} onSubmit={(i) => void submit(i)} />;
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
