"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { API_ENABLED, SITE_NAME } from "@/lib/constants";
import { getPublicPost } from "@/lib/posts/api";
import { decodeSegment } from "@/lib/posts/format";
import { useLoad } from "@/lib/research/hooks";
import { useRegisterPage } from "@/lib/research/pageContext";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import PostArticle from "@/components/posts/PostArticle";
import { NO_API_POSTS } from "@/components/posts/PublicPostFeed";

function Loaded({ slug }: { slug: string }) {
  const { result } = useLoad(`public-post:${slug}`, () => getPublicPost(slug));
  const title = result?.ok ? result.data.title : null;
  // 대화창 문맥: 공개 글은 slug가 아니라 postId로 보낸다(#28 계약). 불러오기 전에는 알리지 않는다.
  useRegisterPage(usePathname(), result?.ok ? { kind: "public_post", resourceId: result.data.postId } : null);
  // 서버 HTML에는 글이 없으므로 탭 제목은 불러온 뒤 정한다.
  useEffect(() => {
    if (title) document.title = `${title} | ${SITE_NAME}`;
  }, [title]);

  if (!result) return <p className="text-sm text-stone-500">불러오는 중...</p>;
  if (!result.ok) {
    if (result.failure.kind === "http" && result.failure.status === 404) {
      return (
        <div className="mx-auto max-w-3xl space-y-3">
          <Notice tone="info">글을 찾을 수 없습니다. 주소가 잘못되었거나 발행이 취소된 글입니다.</Notice>
          <Link href="/posts/" className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
            전체 글 보기 →
          </Link>
        </div>
      );
    }
    return <ErrorNotice failure={result.failure} prefix="글을 불러오지 못했습니다." />;
  }
  return (
    <>
      <PostArticle post={result.data} />
      <p className="mx-auto mt-10 max-w-3xl border-t border-stone-200 pt-8 text-center text-sm text-stone-500">
        좋아요와 댓글 기능은 새 서버로 옮기는 중입니다(준비 중).
      </p>
    </>
  );
}

export default function PostDetail() {
  const params = useParams<{ slug: string }>();
  if (!API_ENABLED) return <Notice tone="info">{NO_API_POSTS}</Notice>;
  return <Loaded slug={decodeSegment(params.slug)} />;
}
