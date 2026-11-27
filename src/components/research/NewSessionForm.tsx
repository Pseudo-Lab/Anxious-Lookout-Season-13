"use client";

import { useState, type FormEvent } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { useAuth } from "@/hooks/useAuth";
import { createSession } from "@/lib/research/api";
import { saveDraft, titleFromMessage } from "@/lib/research/chatState";
import { useMutation, useRestoredRequest } from "@/lib/research/hooks";
import { LIMITS } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";
import { useChat } from "@/components/chat/ChatProvider";

/**
 * 새 개인 대화 세션. publicDocumentId가 있으면 그 공개 문서를 첫 맥락으로 시작한다.
 * #119 N1: 제목 기본값은 더 이상 페이지 이름이 아니다. 첫 질문을 적으면 그 첫 줄이 대화 제목이 되고(최대 60자),
 * 같은 글이 새 대화의 입력창에 그대로 들어가 있어 보내기만 누르면 된다(자동으로 보내지는 않는다).
 */
export default function NewSessionForm({ publicDocumentId }: { publicDocumentId?: string }) {
  const { openSession } = useChat();
  const { user } = useAuth();
  const scope = `create:session:${publicDocumentId ?? "-"}`;
  // 결과 미확인으로 보관된 생성 요청에는 보낸 body(title·publicDocumentId)와 별도로 첫 질문 전체(message)를 남긴다.
  // 새로고침 뒤에는 제목(60자)이 아니라 첫 질문 전체를 되살린다(이전 보관분은 message가 없어 제목으로 대신한다).
  const restored = useRestoredRequest<{ title?: string; message?: string }>(scope);
  const { run, busy } = useMutation(scope);
  const [message, setMessage] = useState(restored?.message ?? restored?.title ?? "");
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text) return;
    const input = { title: titleFromMessage(text).slice(0, LIMITS.title), ...(publicDocumentId && { publicDocumentId }) };
    // 같은 요청 판별(fingerprint)과 전송 body는 input 그대로다. 보관 값에만 첫 질문 전체를 더한다(같은 key로 결과를 다시 확인).
    const res = await run(`session:create:${JSON.stringify(input)}`, (ctx) => createSession(input, ctx), { ...input, message: text });
    if (res.ok) {
      if (user) saveDraft(user.accountId, res.data.id, text);
      setMessage("");
      openSession(res.data.id);
    } else {
      setFailure(res.failure);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <div className="flex gap-2">
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          maxLength={LIMITS.content}
          required
          placeholder="무엇을 알아볼까요?"
          title="첫 줄이 대화 제목이 됩니다"
          aria-label="새 대화 제목(첫 질문)"
          className="min-w-0 flex-1 rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={busy || message.trim() === ""}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "만드는 중..." : "새 대화"}
        </button>
      </div>
      {failure && <ErrorNotice failure={failure} prefix="대화를 만들지 못했습니다." />}
      {restored && !failure && (
        <p className="text-xs text-amber-700">
          이전 대화 만들기 요청의 결과를 확인하지 못했습니다. 같은 내용으로 다시 만들면 중복으로 만들어지지 않습니다.
        </p>
      )}
    </form>
  );
}
