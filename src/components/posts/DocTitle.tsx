"use client";

import { getDocument } from "@/lib/research/api";
import { useLoad } from "@/lib/research/hooks";

/** 연결 문서 ID의 현재 제목(본인 문서). 읽지 못하면(보관·삭제·권한) 그렇다고 표시한다. */
export default function DocTitle({ id }: { id: string }) {
  const { result } = useLoad(`document:${id}`, () => getDocument(id));
  if (!result) return <span className="text-stone-400">문서 확인 중...</span>;
  if (!result.ok) return <span className="text-stone-500">(열 수 없는 문서)</span>;
  return (
    <span>
      {result.data.title || "(제목 없음)"}
      {result.data.archived && <span className="ml-1 text-xs text-amber-700">(보관됨)</span>}
    </span>
  );
}
