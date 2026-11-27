import Link from "next/link";
import type { ReactNode } from "react";

/** 글 쓰기 화면 공통 틀. 편집 중에는 나란히 보기를 위해 페이지 폭을 넓힌다(data-editing). */
export default function WriteFrame({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto max-w-3xl has-[[data-editing]]:max-w-6xl">
      {children}
      <p className="mt-10 text-xs text-stone-500">
        글은 <Link href="/research/" className="underline">내 연구</Link>의 문서를 바탕으로 씁니다. 자료 → 문서 → 글 순서로 정리하고, 어느 단계에서든 대화창에서 도움을 받을 수 있습니다.
      </p>
    </div>
  );
}
