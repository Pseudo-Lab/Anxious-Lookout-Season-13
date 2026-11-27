"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { decodeSegment } from "@/lib/posts/format";
import PublicPostFeed from "@/components/posts/PublicPostFeed";

// #119 E2: 글 수(목록 응답 total), 다른 태그로 가는 길(태그 목록), 글 목록으로 돌아가기.
export default function TagPosts() {
  const params = useParams<{ tag: string }>();
  const tag = decodeSegment(params.tag);
  // 클라이언트 이동에서도 metadata와 같은 제목을 유지한다.
  useDocumentTitle(`# ${tag}`);
  return (
    <>
      <nav aria-label="태그 둘러보기" className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-sm font-medium">
        <Link href="/posts/" className="text-indigo-600 hover:text-indigo-800">
          ← 전체 글 목록
        </Link>
        <Link href="/tags/" className="text-indigo-600 hover:text-indigo-800">
          다른 태그 보기
        </Link>
      </nav>
      <h1 className="mb-8 text-3xl font-bold tracking-tight">
        <span className="text-stone-500">#</span> {tag}
      </h1>
      <PublicPostFeed tag={tag} showTotal />
    </>
  );
}
