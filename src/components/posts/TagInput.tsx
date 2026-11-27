"use client";

import { useDeferredValue, useId, useState } from "react";
import { listPostTags } from "@/lib/posts/api";
import { useLoad } from "@/lib/research/hooks";
import { inputClass } from "@/components/research/ItemForms";

export const TAG_MAX = 20;
export const TAG_LEN = 50;

/** 태그 입력: Enter·쉼표로 추가, ×로 제거. 앞뒤 공백을 지우고 중복은 넣지 않는다(서버도 같은 규칙, 대소문자는 보존). */
export default function TagInput({ tags, onChange, readOnly }: { tags: string[]; onChange: (tags: string[]) => void; readOnly?: boolean }) {
  const id = useId();
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  // #119 O10(D10): 입력한 글자로 이미 쓰인 태그를 제안한다(대소문자 규칙은 그대로라 같은 태그를 고르면 흩어지지 않는다).
  const listId = `${id}-suggestions`;
  const query = useDeferredValue(text.trim().slice(0, 50));
  const suggest = useLoad(`post-tag-suggest:${query}`, () =>
    query ? listPostTags(null, query) : Promise.resolve({ ok: true as const, status: 200, data: { items: [], nextCursor: null } })
  );
  const suggestions = (suggest.result?.ok ? suggest.result.data.items : []).filter((t) => !tags.includes(t.tag)).slice(0, 10);

  function add(raw: string) {
    const parts = raw.split(",").map((t) => t.trim()).filter(Boolean);
    if (!parts.length) return;
    const next = [...tags];
    for (const t of parts) {
      if (t.length > TAG_LEN) {
        setNote(`태그는 ${TAG_LEN}자까지입니다.`);
        continue;
      }
      if (next.includes(t)) continue;
      if (next.length >= TAG_MAX) {
        setNote(`태그는 ${TAG_MAX}개까지입니다.`);
        break;
      }
      next.push(t);
    }
    setText("");
    if (next.length !== tags.length) onChange(next);
  }

  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-stone-900">
        태그
      </label>
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-stone-300 px-2 py-1.5">
        {tags.map((t) => (
          <span key={t} className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700">
            {t}
            {!readOnly && (
              <button type="button" aria-label={`태그 ${t} 빼기`} onClick={() => onChange(tags.filter((x) => x !== t))} className="text-indigo-400 hover:text-indigo-800">
                ×
              </button>
            )}
          </span>
        ))}
        <input
          id={id}
          value={text}
          readOnly={readOnly}
          list={listId}
          autoComplete="off"
          onChange={(e) => {
            setNote(null);
            const v = e.target.value;
            if (v.includes(",")) add(v);
            // 제안 목록에서 고르면 바로 태그로 넣는다(입력 이벤트 종류로 구분: 고르기는 insertReplacementText이거나 종류가 없다)
            else if (suggestions.some((t) => t.tag === v) && (e.nativeEvent as InputEvent).inputType !== "insertText" && (e.nativeEvent as InputEvent).inputType !== "deleteContentBackward") add(v);
            else setText(v);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              add(text);
            } else if (e.key === "Backspace" && !text && tags.length && !readOnly) onChange(tags.slice(0, -1));
          }}
          onBlur={() => add(text)}
          placeholder={tags.length ? "" : "예: AI, 논문리뷰 (Enter 또는 쉼표로 추가)"}
          className={`${inputClass} min-w-32 flex-1 border-0 px-1 py-0.5 focus:ring-0`}
        />
        <datalist id={listId}>
          {suggestions.map((t) => (
            <option key={t.tag} value={t.tag}>
              {t.tag} (글 {t.count}편)
            </option>
          ))}
        </datalist>
      </div>
      <span className="mt-1 block text-xs text-stone-500">{note ?? `최대 ${TAG_MAX}개, 각 ${TAG_LEN}자까지. 이미 쓰인 태그가 제안되면 그것을 고르세요(대소문자가 다르면 다른 태그가 됩니다).`}</span>
    </div>
  );
}
