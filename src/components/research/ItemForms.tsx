"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import type { FetchFailure } from "@/lib/api/client";
import type { DocumentInput, MaterialInput } from "@/lib/research/api";
import { CONTENT_KINDS, CONTENT_KIND_LABELS, LIMITS, type ContentKind } from "@/lib/research/types";
import { fromLocalInput, toLocalInput } from "@/lib/research/format";
import ErrorNotice from "@/components/research/ErrorNotice";

const inputClass = "w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-stone-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-stone-500">{hint}</span>}
    </label>
  );
}

function FormActions({ busy, submitLabel, onCancel }: { busy: boolean; submitLabel: string; onCancel?: () => void }) {
  return (
    <div className="flex gap-3">
      <button
        type="submit"
        disabled={busy}
        className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
      >
        {busy ? "저장 중..." : submitLabel}
      </button>
      {onCancel && (
        <button type="button" onClick={onCancel} disabled={busy} className="rounded-lg px-4 py-2 text-sm text-stone-600 hover:bg-stone-100">
          취소
        </button>
      )}
    </div>
  );
}

const contentHint = "Markdown으로 저장됩니다. 인용 문구와 원문 링크는 본문에 그대로 보존됩니다.";

function isHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/** 자료 작성·수정 폼. 수정 저장은 새 내용 버전을 만든다. 실패 시 입력을 그대로 유지한다. */
export function MaterialForm({
  initial,
  submitLabel,
  busy,
  failure,
  onSubmit,
  onCancel,
}: {
  initial?: MaterialInput;
  submitLabel: string;
  busy: boolean;
  failure: FetchFailure | null;
  onSubmit: (input: MaterialInput) => void;
  onCancel?: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [sourceUrl, setSourceUrl] = useState(initial?.sourceUrl ?? "");
  const [collectedAt, setCollectedAt] = useState(() => toLocalInput(initial?.collectedAt ?? new Date().toISOString()));
  const [contentKind, setContentKind] = useState<ContentKind>(initial?.contentKind ?? "excerpt");
  const [content, setContent] = useState(initial?.content ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    const collected = fromLocalInput(collectedAt);
    if (!isHttpUrl(sourceUrl.trim())) return setLocalError("출처 URL은 http:// 또는 https:// 주소여야 합니다.");
    if (!collected) return setLocalError("수집 시각을 입력해 주세요.");
    setLocalError(null);
    onSubmit({ title: title.trim(), sourceUrl: sourceUrl.trim(), collectedAt: collected, contentKind, content });
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="제목">
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={LIMITS.title} required className={inputClass} />
      </Field>
      <Field label="출처 URL">
        <input
          type="url"
          value={sourceUrl}
          onChange={(e) => setSourceUrl(e.target.value)}
          required
          placeholder="https://"
          className={inputClass}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="수집 시각" hint="브라우저 시간대 기준으로 입력하며 UTC로 저장됩니다.">
          <input
            type="datetime-local"
            value={collectedAt}
            onChange={(e) => setCollectedAt(e.target.value)}
            required
            className={inputClass}
          />
        </Field>
        <Field label="저장 범위">
          <select value={contentKind} onChange={(e) => setContentKind(e.target.value as ContentKind)} className={inputClass}>
            {CONTENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {CONTENT_KIND_LABELS[k]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="내용" hint={contentHint}>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          maxLength={LIMITS.content}
          rows={16}
          className={`${inputClass} font-mono`}
        />
      </Field>
      {localError && <p role="alert" className="text-sm text-red-700">{localError}</p>}
      {failure && <ErrorNotice failure={failure} prefix="저장하지 못했습니다." />}
      <FormActions busy={busy} submitLabel={submitLabel} onCancel={onCancel} />
    </form>
  );
}

/** 문서 작성·수정 폼. 수정 저장은 새 내용 버전을 만든다. */
export function DocumentForm({
  initial,
  submitLabel,
  busy,
  failure,
  onSubmit,
  onCancel,
}: {
  initial?: DocumentInput;
  submitLabel: string;
  busy: boolean;
  failure: FetchFailure | null;
  onSubmit: (input: DocumentInput) => void;
  onCancel?: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [content, setContent] = useState(initial?.content ?? "");

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit({ title: title.trim(), content });
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="제목">
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={LIMITS.title} required className={inputClass} />
      </Field>
      <Field label="본문" hint={contentHint}>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          maxLength={LIMITS.content}
          rows={20}
          className={`${inputClass} font-mono`}
        />
      </Field>
      {failure && <ErrorNotice failure={failure} prefix="저장하지 못했습니다." />}
      <FormActions busy={busy} submitLabel={submitLabel} onCancel={onCancel} />
    </form>
  );
}
