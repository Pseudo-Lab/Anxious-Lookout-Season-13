"use client";

import { useDeferredValue, useEffect, useId, useRef, useState, type FormEvent, type ReactNode, type Ref } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { fetchUrlMetadata, urlMetadataError, type DocumentInput, type MaterialInput, type UrlMetadata } from "@/lib/research/api";
import { useAuth } from "@/hooks/useAuth";
import { CONTENT_KINDS, CONTENT_KIND_LABELS, LIMITS, type ContentKind } from "@/lib/research/types";
import { fromLocalInput, toLocalInput } from "@/lib/research/format";
import { CSL_TYPES, cpLength, cslTypeLabel, fromBibliographyForm, toBibliographyForm, type BibliographyForm } from "@/lib/research/bibliography";
import { readSummary } from "@/lib/research/wikiInput";
import ErrorNotice from "@/components/research/ErrorNotice";
import SafeMarkdown from "@/components/research/SafeMarkdown";

export const inputClass = "w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-stone-900">{label}</span>
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
        <button type="button" onClick={onCancel} disabled={busy} className="rounded-lg px-4 py-2 text-sm text-stone-900 hover:bg-stone-100">
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

/** 글자 수 표시(Unicode code point 기준, 계약 단위). 넘치면 강조한다. 입력을 자르지 않고 저장 전에 확인한다. */
export function CharCount({ value, limit }: { value: string; limit: number }) {
  const n = cpLength(value.trim());
  return (
    <span aria-hidden="true" className={`text-xs tabular-nums ${n > limit ? "font-medium text-red-700" : "text-stone-500"}`}>
      {n}/{limit}
    </span>
  );
}

/** #105 한 줄 요약(자료·문서). 목록과 에이전트 index에 보인다. maxLength(UTF-16 단위)는 쓰지 않는다. */
export function SummaryField({ value, onChange, readOnly, hint }: { value: string; onChange: (v: string) => void; readOnly?: boolean; hint?: string }) {
  return (
    <Field label="한 줄 요약 (선택)" hint={hint ?? `목록과 에이전트 index에 보입니다. ${LIMITS.summary}자 이하, 한 줄.`}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/[\r\n]+/g, " "))}
        readOnly={readOnly}
        className={`${inputClass} read-only:bg-stone-50`}
      />
      <CharCount value={value} limit={LIMITS.summary} />
    </Field>
  );
}

const BIB_TEXT_FIELDS: { key: Exclude<keyof BibliographyForm, "type" | "authors" | "issued">; label: string; placeholder?: string; wide?: boolean }[] = [
  { key: "title", label: "원문 표제", placeholder: "자료 제목과 다를 때", wide: true },
  { key: "container-title", label: "게재처", placeholder: "학술지·학회·매체·사이트 이름", wide: true },
  { key: "publisher", label: "출판사·발행 기관" },
  { key: "volume", label: "권" },
  { key: "issue", label: "호" },
  { key: "page", label: "쪽", placeholder: "예: 12-34" },
  { key: "number", label: "번호", placeholder: "보고서 번호, arXiv:2401.12345" },
  { key: "DOI", label: "DOI", placeholder: "10.1000/xyz123" },
  { key: "ISBN", label: "ISBN" },
  { key: "ISSN", label: "ISSN" },
];

type BibTextKey = (typeof BIB_TEXT_FIELDS)[number]["key"];

const BIB_LABELS: Partial<Record<keyof BibliographyForm, string>> = {
  type: "서지 종류",
  authors: "저자",
  issued: "발행일",
  ...Object.fromEntries(BIB_TEXT_FIELDS.map((f) => [f.key, f.label])),
};

/** #119 K3 상태: 한 번이라도 503(not_ready)이면 이 화면에서는 버튼을 숨긴다. */
function useUrlAutofill() {
  const { csrfToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [available, setAvailable] = useState(true);
  async function run(url: string): Promise<UrlMetadata | null> {
    if (!csrfToken) {
      setMessage("로그인 상태를 확인해 주세요.");
      return null;
    }
    setBusy(true);
    setMessage("출처 페이지를 읽는 중입니다. 몇 초 걸릴 수 있습니다.");
    const res = await fetchUrlMetadata(url, csrfToken);
    setBusy(false);
    if (res.ok) return res.data;
    if (res.failure.kind === "http" && res.failure.code === "not_ready") setAvailable(false);
    setMessage(urlMetadataError(res.failure));
    return null;
  }
  function done(filled: string[]) {
    setMessage(filled.length ? `채웠습니다: ${filled.join(", ")}. 저장 전에 확인하세요.` : "새로 채울 빈 칸이 없습니다(이미 입력한 값은 바꾸지 않습니다).");
  }
  function stale() {
    setMessage("기다리는 동안 출처 URL이 바뀌어 가져온 결과를 쓰지 않았습니다. 다시 눌러 주세요.");
  }
  return { busy, message, available, run, done, stale };
}

// #119 K2: 서지 종류마다 보통 쓰는 칸만 보인다(예: 웹페이지에는 ISBN·ISSN이 없다). 모르는 종류·기타 문서는 모두 보인다.
const FIELDS_BY_TYPE: Record<string, BibTextKey[]> = {
  "article-journal": ["title", "container-title", "volume", "issue", "page", "DOI", "ISSN"],
  "paper-conference": ["title", "container-title", "publisher", "page", "DOI"],
  article: ["title", "container-title", "number", "DOI"],
  book: ["title", "publisher", "volume", "ISBN", "DOI"],
  chapter: ["title", "container-title", "publisher", "page", "ISBN", "DOI"],
  report: ["title", "publisher", "number", "DOI"],
  thesis: ["title", "publisher"],
  webpage: ["title", "container-title"],
  "post-weblog": ["title", "container-title"],
  "article-newspaper": ["title", "container-title", "issue", "page", "ISSN"],
  "article-magazine": ["title", "container-title", "volume", "issue", "page", "ISSN"],
  dataset: ["title", "publisher", "number", "DOI"],
  software: ["title", "publisher", "number", "DOI"],
  motion_picture: ["title", "container-title", "publisher"],
  interview: ["title", "container-title"],
  legislation: ["title", "container-title", "number", "page"],
};

/** 이 종류에서 보일 칸. 종류에 없는 칸이라도 이미 값이 있으면 보인다(숨겨서 지워지지 않게, 아래에서 알린다). */
export function bibFieldsFor(type: string, value: Partial<Record<BibTextKey, string>>): { shown: BibTextKey[]; extra: BibTextKey[] } {
  const all = BIB_TEXT_FIELDS.map((f) => f.key);
  const wanted = type === "" ? [] : (FIELDS_BY_TYPE[type] ?? all);
  const extra = all.filter((k) => !wanted.includes(k) && (value[k] ?? "").trim() !== "");
  return { shown: all.filter((k) => wanted.includes(k) || extra.includes(k)), extra };
}

/** #105 자료 서지정보 입력(CSL-JSON 부분집합). URL·접근일은 출처 URL·수집 시각을 그대로 쓴다. #119 K1: 서지정보가 없으면 접혀 있다. */
function BibliographyFields({ value, onChange, forceOpen = false }: { value: BibliographyForm; onChange: (v: BibliographyForm) => void; forceOpen?: boolean }) {
  const set = (patch: Partial<BibliographyForm>) => onChange({ ...value, ...patch });
  const { shown, extra } = bibFieldsFor(value.type, value);
  return (
    <details open={value.type !== "" || forceOpen} className="rounded-lg p-3 ring-1 ring-stone-200">
      <summary className="cursor-pointer text-sm font-medium text-stone-900">서지정보 (선택)</summary>
      <div className="@container mt-3 space-y-3">
        <p className="text-xs text-stone-500">인용 형식에 쓰입니다. URL과 접근일은 위의 출처 URL·수집 시각을 그대로 씁니다.</p>
        <div className="grid gap-3 @lg:grid-cols-2">
          <Field label="서지 종류">
            <select value={value.type} onChange={(e) => set({ type: e.target.value })} className={inputClass}>
              <option value="">서지정보 없음</option>
              {value.type && !CSL_TYPES.includes(value.type) && <option value={value.type}>{value.type}</option>}
              {CSL_TYPES.map((t) => (
                <option key={t} value={t}>
                  {cslTypeLabel(t)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="발행일" hint="연도만, 또는 2024-03, 2024-03-15">
            <input value={value.issued} onChange={(e) => set({ issued: e.target.value })} placeholder="2024" className={inputClass} />
          </Field>
        </div>
        {/* #119 K4: 안내는 예시 한 줄, 자세한 규칙은 펼쳐 본다 */}
        <div>
          <Field label="저자" hint="한 줄에 한 명. 예: Kim, Minji">
            <textarea value={value.authors} onChange={(e) => set({ authors: e.target.value })} rows={3} className={inputClass} />
          </Field>
          <details className="mt-1 text-xs text-stone-600">
            <summary className="cursor-pointer select-none">저자 입력 규칙</summary>
            <p className="mt-1">
              사람은 ‘성, 이름’(예: Kim, Minji 또는 홍, 길동), 기관이나 나눌 수 없는 이름은 그대로 씁니다. 쉼표가 든 기관 이름은 앞에 =를 붙입니다(예: =Research, Inc.).
            </p>
          </details>
        </div>
        {value.type === "" && <p className="text-xs text-stone-600">서지 종류를 고르면 그 종류에 필요한 칸이 보입니다.</p>}
        {extra.length > 0 && (
          <p className="text-xs text-amber-800">
            이 종류에는 보통 쓰지 않는 칸에 값이 있습니다({extra.map((k) => BIB_TEXT_FIELDS.find((f) => f.key === k)?.label).join(", ")}). 지우지 않으면 그대로 저장됩니다.
          </p>
        )}
        <div className="grid gap-3 @lg:grid-cols-2">
          {BIB_TEXT_FIELDS.filter((f) => shown.includes(f.key)).map((f) => (
            <div key={f.key} className={f.wide ? "@lg:col-span-2" : undefined}>
              <Field label={f.label}>
                <input value={value[f.key]} onChange={(e) => set({ [f.key]: e.target.value })} placeholder={f.placeholder} className={inputClass} />
              </Field>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
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
  const [summary, setSummary] = useState(initial?.summary ?? "");
  const [bibliography, setBibliography] = useState(() => toBibliographyForm(initial?.bibliography ?? null));
  // 처음 값(그대로 두면 저장값을 손대지 않고 다시 보낸다)
  const [initialBibliography] = useState(() => JSON.stringify(toBibliographyForm(initial?.bibliography ?? null)));
  const [localError, setLocalError] = useState<string | null>(null);
  const [bibOpen, setBibOpen] = useState(false);
  const autofill = useUrlAutofill();

  // 응답을 기다리는 동안 바뀐 입력을 덮지 않도록, 응답 시점의 최신 값을 ref로 본다(리뷰 round3).
  const latest = useRef({ title, sourceUrl, bibliography });
  useEffect(() => {
    latest.current = { title, sourceUrl, bibliography };
  });

  /** #119 K3: 출처 페이지의 제목·서지정보로 응답 시점에 비어 있는 칸만 채운다. 그사이 출처를 바꿨으면 결과를 버린다. 저장은 하지 않는다. */
  async function fillFromSource() {
    const url = sourceUrl.trim();
    if (!isHttpUrl(url)) return setLocalError("출처 URL은 http:// 또는 https:// 주소여야 합니다.");
    setLocalError(null);
    const meta = await autofill.run(url);
    if (!meta) return;
    const now = latest.current;
    if (now.sourceUrl.trim() !== url) return autofill.stale();
    const filled: string[] = [];
    if (!now.title.trim() && meta.title) {
      setTitle(meta.title.slice(0, LIMITS.title));
      filled.push("제목");
    }
    if (meta.bibliography) {
      const incoming = toBibliographyForm(meta.bibliography);
      const next = { ...now.bibliography };
      for (const key of Object.keys(incoming) as (keyof BibliographyForm)[]) {
        if (!next[key].trim() && incoming[key].trim()) {
          next[key] = incoming[key];
          filled.push(BIB_LABELS[key] ?? key);
        }
      }
      if (filled.some((f) => f !== "제목")) {
        setBibliography(next);
        setBibOpen(true);
      }
    }
    autofill.done(filled);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const collected = fromLocalInput(collectedAt);
    if (!isHttpUrl(sourceUrl.trim())) return setLocalError("출처 URL은 http:// 또는 https:// 주소여야 합니다.");
    if (!collected) return setLocalError("수집 시각을 입력해 주세요.");
    const sum = readSummary(summary);
    if (!sum.ok) return setLocalError(sum.error);
    // 서지정보 칸을 건드리지 않았으면 저장된 값을 그대로 보낸다(다른 칸만 고쳐도 표현이 바뀌지 않게).
    const untouched = JSON.stringify(bibliography) === initialBibliography;
    const bib = untouched ? ({ ok: true, value: initial?.bibliography ?? null } as const) : fromBibliographyForm(bibliography, initial?.bibliography ?? null);
    if (!bib.ok) return setLocalError(bib.error);
    setLocalError(null);
    // 수정은 생략하면 최신 값을 이어 받으므로 화면의 값을 항상 명시한다(null = 지움). 새 자료는 값이 있을 때만 보낸다.
    const extra = initial
      ? { summary: sum.value, bibliography: bib.value }
      : { ...(sum.value ? { summary: sum.value } : {}), ...(bib.value ? { bibliography: bib.value } : {}) };
    onSubmit({ title: title.trim(), sourceUrl: sourceUrl.trim(), collectedAt: collected, contentKind, content, ...extra });
  }

  return (
    <form onSubmit={submit} className="@container space-y-4">
      <Field label="제목">
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={LIMITS.title} required className={inputClass} />
      </Field>
      <div>
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
        {autofill.available && (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void fillFromSource()}
              disabled={autofill.busy || !sourceUrl.trim()}
              className="rounded-md border border-stone-300 px-2 py-1 text-xs text-stone-800 hover:bg-stone-50 disabled:opacity-50"
            >
              {autofill.busy ? "가져오는 중..." : "출처에서 제목·서지정보 채우기"}
            </button>
            <span role="status" className="text-xs text-stone-600">
              {autofill.message}
            </span>
          </div>
        )}
      </div>
      <div className="grid gap-4 @lg:grid-cols-2">
        {/* #119 K5: 저장 방식(UTC) 안내는 사용자에게 필요하지 않다 */}
        <Field label="수집 시각">
          <input
            type="datetime-local"
            value={collectedAt}
            onChange={(e) => setCollectedAt(e.target.value)}
            required
            className={inputClass}
          />
        </Field>
        {/* #119 K6 */}
        <Field label="저장 범위" hint="전체 = 원문 전체 · 발췌 = 일부를 옮겨 적음 · 요약 = 내 말로 줄여 적음">
          <select value={contentKind} onChange={(e) => setContentKind(e.target.value as ContentKind)} className={inputClass}>
            {CONTENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {CONTENT_KIND_LABELS[k]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <SummaryField value={summary} onChange={setSummary} />
      <BibliographyFields value={bibliography} onChange={setBibliography} forceOpen={bibOpen} />
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

type EditorView = "split" | "write" | "preview";
const VIEW_LABELS: Record<EditorView, string> = { split: "나란히 보기", write: "작성만", preview: "미리보기만" };

/**
 * 문서 본문 편집기: 원문(Markdown) 입력과 미리보기. 미리보기는 저장된 본문과 같은 SafeMarkdown 규칙으로 그린다.
 * 넓은 화면에서는 좌우, 좁은 화면에서는 위아래로 놓인다(편집기 폭 기준 container query).
 */
/** ownImages: 글(#28)의 자체 업로드 이미지를 미리보기에 띄운다(공개 화면과 같은 규칙). */
export function MarkdownEditor({
  value,
  onChange,
  readOnly = false,
  ownImages = false,
  textareaRef,
}: {
  value: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
  ownImages?: boolean;
  // 커서 위치에 내용을 넣을 때(#28 이미지)
  textareaRef?: Ref<HTMLTextAreaElement>;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const [view, setView] = useState<EditorView>("split");
  // 입력은 바로 반영하고 미리보기 렌더는 뒤따른다(긴 문서에서도 타이핑이 밀리지 않게).
  const preview = useDeferredValue(value);
  const previewBox = useRef<HTMLDivElement>(null);

  // 나란히 볼 때 본문 스크롤 위치를 미리보기에 비율로 맞춘다.
  function syncScroll(e: { currentTarget: HTMLTextAreaElement }) {
    const box = previewBox.current;
    if (view !== "split" || !box) return;
    const t = e.currentTarget;
    const ratio = t.scrollHeight > t.clientHeight ? t.scrollTop / (t.scrollHeight - t.clientHeight) : 0;
    box.scrollTop = ratio * (box.scrollHeight - box.clientHeight);
  }

  return (
    <div className="@container block">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-stone-900">
          본문
        </label>
        <div role="group" aria-label="편집 화면" className="flex rounded-lg bg-stone-100 p-0.5 text-xs">
          {(Object.keys(VIEW_LABELS) as EditorView[]).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={`rounded-md px-2.5 py-1 ${view === v ? "bg-white font-medium text-stone-900 shadow-sm" : "text-stone-900 hover:text-stone-900"}`}
            >
              {VIEW_LABELS[v]}
            </button>
          ))}
        </div>
      </div>
      <div className={view === "split" ? "grid gap-3 @3xl:grid-cols-2" : ""}>
        {/* 미리보기만 볼 때도 입력 내용은 그대로 둔다(숨기기만 한다). */}
        <textarea
          id={id}
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onScroll={syncScroll}
          maxLength={LIMITS.content}
          rows={20}
          aria-describedby={hintId}
          readOnly={readOnly}
          hidden={view === "preview"}
          className={`${inputClass} h-[60vh] min-h-64 resize-y font-mono read-only:bg-stone-50`}
        />
        {view !== "write" && (
          <div
            ref={previewBox}
            role="region"
            aria-label="미리보기"
            tabIndex={0}
            className="h-[60vh] min-h-64 overflow-auto rounded-lg bg-white px-4 py-3 ring-1 ring-stone-200"
          >
            {preview.trim() ? (
              <SafeMarkdown content={preview} ownImages={ownImages} />
            ) : (
              <p className="text-sm text-stone-500">본문을 쓰면 여기에 저장될 모습이 보입니다.</p>
            )}
          </div>
        )}
      </div>
      <span id={hintId} className="mt-1 block text-xs text-stone-500">
        {contentHint} 미리보기는 저장 후 화면과 같은 규칙(외부 이미지·HTML 미표시{ownImages ? ", 직접 올린 이미지만 표시" : ""})으로 보입니다.
      </span>
    </div>
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
      <MarkdownEditor value={content} onChange={setContent} />
      {failure && <ErrorNotice failure={failure} prefix="저장하지 못했습니다." />}
      <FormActions busy={busy} submitLabel={submitLabel} onCancel={onCancel} />
    </form>
  );
}
