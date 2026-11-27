"use client";

import { useEffect } from "react";
import { SITE_NAME } from "@/lib/constants";

/** "제목 | 초조한 전망대". 빈 제목이면 사이트 이름만. */
export function pageTitle(title: string | null | undefined): string {
  const t = title?.trim();
  return t ? `${t} | ${SITE_NAME}` : SITE_NAME;
}

/**
 * #119 A3: 불러온 데이터(글·자료·문서 제목)로 탭 제목을 정한다. null이면 건드리지 않는다(정적 metadata 제목이 남는다).
 * 동적 경로의 metadata <title>은 화면보다 늦게 붙을 수 있어(Next 메타데이터 스트리밍) 한 번만 쓰면 덮인다.
 * 이 화면이 떠 있는 동안 <head>의 제목이 바뀌면 다시 쓴다. 화면을 떠나면(unmount) 더 이상 고치지 않는다.
 */
export function useDocumentTitle(title: string | null | undefined) {
  const text = title == null ? null : pageTitle(title);
  useEffect(() => {
    if (text === null) return;
    const apply = () => {
      if (document.title !== text) document.title = text;
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [text]);
}
