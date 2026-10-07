"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { FetchFailure } from "@/lib/api/client";
import { createSession } from "@/lib/research/api";
import { useMutation, useRestoredRequest } from "@/lib/research/hooks";
import { LIMITS } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";

// 새 개인 대화 세션. publicDocumentId가 있으면 그 공개 문서를 첫 맥락으로 시작한다.
export default function NewSessionForm({
  publicDocumentId,
  defaultTitle = "",
}: {
  publicDocumentId?: string;
  defaultTitle?: string;
}) {
  const router = useRouter();
  const scope = `create:session:${publicDocumentId ?? "-"}`;
  const restored = useRestoredRequest<{ title?: string }>(scope);
  const { run, busy } = useMutation(scope);
  const [title, setTitle] = useState(restored?.title ?? defaultTitle);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const input = { title: title.trim(), ...(publicDocumentId && { publicDocumentId }) };
    const res = await run(`session:create:${JSON.stringify(input)}`, (ctx) => createSession(input, ctx), input);
    if (res.ok) {
      router.push(`/research/session/?id=${encodeURIComponent(res.data.id)}`);
    } else {
      setFailure(res.failure);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <div className="flex gap-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={LIMITS.title}
          required
          placeholder="새 대화 제목"
          aria-label="새 대화 제목"
          className="min-w-0 flex-1 rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={busy || title.trim() === ""}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "만드는 중..." : "새 대화"}
        </button>
      </div>
      {failure && <ErrorNotice failure={failure} prefix="대화를 만들지 못했습니다." />}
      {restored && !failure && (
        <p className="text-xs text-amber-700">
          이전 대화 만들기 요청의 결과를 확인하지 못했습니다. 같은 제목으로 다시 만들면 중복으로 만들어지지 않습니다.
        </p>
      )}
    </form>
  );
}
