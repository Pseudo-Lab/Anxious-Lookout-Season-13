"use client";

// 새 문서·새 글 편집 화면 공통(#22, #28): 주소(?draft=)가 가리키는 서버 초안에 편집기를 묶는다.
// 편집기가 첫 자동 저장으로 초안을 만들면 주소를 그 초안으로 바꾸고, 다른 초안 주소로 옮겨 가면 이전 편집기를 내린다.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

// 지금 화면의 편집기와 그 편집기가 쓰는 초안.
// bound: 편집기가 저장하는 초안 ID(새 편집기는 첫 자동 저장 전까지 null).
// transit: 주소가 아직 따라오지 않은 이전 주소 값(편집기가 방금 만든 초안으로 바꾸는 중이거나, 찾지 못한 초안 주소에서 새로 시작한 경우).
// 주소(?draft=)가 bound·transit과 맞지 않는 동안에는 편집기를 그리지 않는다(다른 초안 주소에서 이 초안을 고치지 않게, 리뷰 F4).
// released: 주소가 맞지 않아 편집기를 한 번 내렸다(다른 초안으로 이동 중 등). 내린 화면 상태는 다시 쓰지 않는다.
// 주소가 다시 같아져도(뒤로 가기) 서버에서 최신 초안을 다시 읽는다(오래된 view.draft나 빈 편집기로 되살리지 않게, 리뷰 F4 잔여).
export interface DraftView<D> {
  key: string;
  draft: D | null;
  bound: string | null;
  transit?: string | null;
  note?: string;
  released?: boolean;
}
const matches = <D,>(v: DraftView<D>, param: string | null) =>
  !v.released && (param === v.bound || (v.transit !== undefined && param === v.transit));

/** 주소의 초안 ID를 확인한 결과. active: 이어 쓸 새 초안 / committed: 이미 저장돼 그 화면으로 / missing: 새로 시작. */
export type DraftLookup<D> = { kind: "active"; draft: D } | { kind: "committed"; href: string } | { kind: "missing" };

export function useAddressedDraft<D extends { id: string }>({
  param,
  newPath,
  lookup,
}: {
  param: string | null;
  // 새 편집 화면 경로(예: "/write/new/")
  newPath: string;
  // 모듈 상수 함수
  lookup: (id: string) => Promise<DraftLookup<D>>;
}) {
  const router = useRouter();
  const [view, setView] = useState<DraftView<D> | null>(param ? null : { key: "new", draft: null, bound: null });
  const viewRef = useRef(view);
  const paramRef = useRef(param);
  useEffect(() => {
    viewRef.current = view;
    paramRef.current = param;
  });

  useEffect(() => {
    const v = viewRef.current;
    let active = true;
    if (v && !v.released && param === v.bound) {
      // 주소가 지금 편집기를 따라왔다. 이전 주소 값은 더 맞추지 않는다.
      if (v.transit !== undefined) void Promise.resolve().then(() => active && setView((cur) => (cur && cur.key === v.key ? { ...cur, transit: undefined } : cur)));
      return () => {
        active = false;
      };
    }
    if (v && !v.released && v.transit !== undefined && param === v.transit) return;
    if (!param) {
      // 다른 초안을 보다가 새 화면으로 왔다
      void Promise.resolve().then(() => active && setView({ key: `new:${Date.now()}`, draft: null, bound: null }));
      return () => {
        active = false;
      };
    }
    void lookup(param).then((r) => {
      if (!active) return;
      if (r.kind === "active") setView({ key: r.draft.id, draft: r.draft, bound: r.draft.id });
      else if (r.kind === "committed") router.replace(r.href);
      else setView({ key: `new:${param}:${Date.now()}`, draft: null, bound: null, transit: param, note: "이어 쓸 초안을 찾지 못해 새로 시작합니다." });
    });
    return () => {
      active = false;
    };
  }, [param, router, lookup]);

  // 주소가 지금 편집기와 맞지 않게 된 첫 렌더에서 화면 상태를 내린다(이전 렌더 정보로 상태를 고치는 React 패턴).
  if (view && !view.released && !matches(view, param)) setView({ ...view, released: true });
  const current = view && matches(view, param) ? view : null;

  /** 편집기가 초안을 만들거나 바꿨을 때. key: 그 편집기의 view.key */
  function onDraft(key: string, d: D) {
    // 이미 다른 편집기로 바뀌었으면(늦은 자동 저장 응답) 주소를 건드리지 않는다.
    const cur = viewRef.current;
    if (!cur || cur.key !== key || cur.bound === d.id) return;
    const from = paramRef.current;
    setView((v) => (v && v.key === key ? { ...v, bound: d.id, transit: from } : v));
    router.replace(`${newPath}?draft=${encodeURIComponent(d.id)}`, { scroll: false });
  }

  function onDiscarded(note: string) {
    setView({ key: `new:${Date.now()}`, draft: null, bound: null, transit: paramRef.current, note });
    router.replace(newPath, { scroll: false });
  }

  return { view: current, onDraft, onDiscarded };
}
