"use client";

import { useDeferredValue, useEffect, useId, useState, type KeyboardEvent } from "react";
import { listDocuments, listMaterials } from "@/lib/research/api";
import { usePagedList } from "@/lib/research/usePagedList";
import type { Endpoint, ItemType } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";

/**
 * #119 J2: 관계 대상 고르기. "제목으로 찾기"와 목록을 합친 검색형 콤보박스(ARIA 1.2 combobox + listbox).
 * 입력하면 서버에서 제목(자료는 원본 주소도)으로 찾고(issue119 §I2 q), ↑/↓로 옮기고 Enter로 고르며 Esc로 닫는다.
 */
export default function TargetCombobox({
  type,
  exclude,
  value,
  onChange,
  fieldClass,
}: {
  type: ItemType;
  exclude?: { type: ItemType; id: string };
  value: Endpoint | null;
  onChange: (v: Endpoint | null) => void;
  fieldClass: string;
}) {
  const id = useId();
  const listId = `${id}-list`;
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const q = useDeferredValue(text.trim());
  const list = usePagedList<{ id: string; title: string }>(`relation-target:${type}:${q}`, (cursor) =>
    type === "material" ? listMaterials(cursor, false, { q: q || undefined, sort: "title" }) : listDocuments(cursor, false, { q: q || undefined, sort: "title" })
  );
  const options = list.items.filter((c) => !(exclude && exclude.type === type && exclude.id === c.id));
  const activeIndex = Math.min(active, Math.max(0, options.length - 1));
  const noun = type === "material" ? "자료" : "문서";
  // 활성 후보가 목록 상자 밖이면 보이게 스크롤한다(키보드로 내려갈 때)
  useEffect(() => {
    if (!open) return;
    document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex, listId, options.length]);

  function choose(i: number) {
    const c = options[i];
    if (!c) return;
    onChange({ type, id: c.id, title: c.title });
    setText("");
    setOpen(false);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      // 마지막 후보에서 더 내려가면 다음 페이지를 불러오고, 불러온 첫 후보로 옮긴다
      if (activeIndex >= options.length - 1 && list.nextCursor && !list.loading) {
        void list.loadMore();
        setActive(options.length);
        return;
      }
      setActive((a) => Math.min(a + 1, options.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && open && options.length > 0) {
      e.preventDefault();
      choose(activeIndex);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    }
  }

  if (value) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-white px-3 py-2 text-sm ring-1 ring-stone-300">
        <span className="text-stone-600">연결할 {noun}:</span>
        <span className="font-medium text-stone-900">{value.title || "(제목 없음)"}</span>
        <button
          type="button"
          onClick={() => onChange(null)}
          className="ml-auto text-xs font-medium text-indigo-600 hover:text-indigo-800"
        >
          다른 {noun} 고르기
        </button>
      </div>
    );
  }

  return (
    <div className="relative">
      <label htmlFor={id} className="sr-only">
        연결할 {noun} 찾기
      </label>
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && options[activeIndex] ? `${listId}-${activeIndex}` : undefined}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        placeholder={`연결할 ${noun}의 제목으로 찾기`}
        autoComplete="off"
        className={`${fieldClass} w-full`}
      />
      <ul
        id={listId}
        role="listbox"
        aria-label={`${noun} 후보`}
        hidden={!open}
        className="absolute inset-x-0 top-full z-20 mt-1 max-h-64 overflow-auto rounded-lg bg-white py-1 text-sm shadow-lg ring-1 ring-stone-200"
      >
        {options.map((c, i) => (
          <li
            key={c.id}
            id={`${listId}-${i}`}
            role="option"
            aria-selected={i === activeIndex}
            // blur 전에 고르도록 mousedown에서 처리한다
            onMouseDown={(e) => {
              e.preventDefault();
              choose(i);
            }}
            onMouseEnter={() => setActive(i)}
            className={`cursor-pointer px-3 py-2 ${i === activeIndex ? "bg-indigo-50 text-indigo-800" : "text-stone-900"}`}
          >
            {c.title || "(제목 없음)"}
          </li>
        ))}
        {list.loaded && options.length === 0 && (
          <li role="presentation" className="px-3 py-2 text-stone-600">
            {q ? `‘${q}’에 맞는 ${noun}가 없습니다.` : `연결할 수 있는 ${noun}가 없습니다.`}
          </li>
        )}
        {!list.loaded && (
          <li role="presentation" className="px-3 py-2 text-stone-600">
            찾는 중...
          </li>
        )}
        {list.nextCursor && (
          <li role="presentation" className="px-3 py-1.5 text-xs text-stone-600">
            {/* 키보드: 마지막 후보에서 ↓를 누르면 다음 후보를 불러온다. 마우스: 아래 버튼 */}
            <button
              type="button"
              tabIndex={-1}
              onMouseDown={(e) => {
                e.preventDefault();
                void list.loadMore();
              }}
              className="font-medium text-indigo-600 hover:text-indigo-800"
            >
              {list.loading ? "불러오는 중..." : list.total !== null ? `${list.total}개 중 ${list.items.length}개 · 더 불러오기` : "더 불러오기"}
            </button>
            <span className="ml-1">(마지막 후보에서 ↓)</span>
          </li>
        )}
      </ul>
      {list.failure && <ErrorNotice failure={list.failure} prefix={`${noun} 목록을 불러오지 못했습니다.`} />}
    </div>
  );
}
