"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { API_ENABLED } from "@/lib/constants";
import { useAuth } from "@/hooks/useAuth";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { getPublicPost } from "@/lib/posts/api";
import { decodeSegment } from "@/lib/posts/format";
import type { PublicPost } from "@/lib/posts/types";
import { writePostPath } from "@/lib/routes";
import { useLoad } from "@/lib/research/hooks";
import { useRegisterPage } from "@/lib/research/pageContext";
import ErrorNotice from "@/components/research/ErrorNotice";
import Notice from "@/components/research/Notice";
import PostArticle from "@/components/posts/PostArticle";
import { PostArticleSkeleton } from "@/components/posts/PostSkeleton";
import { NO_API_POSTS } from "@/components/posts/PublicPostFeed";

/**
 * #119 A6: 본인 글이면 편집 화면으로 가는 "수정" 링크. 공개 응답의 author.login과 로그인 계정 login이 같을 때만 보인다
 * (계약 issue119 §0: 실제 권한은 편집 화면의 소유자 전용 API가 판단한다. login이 바뀌면 다음 발행까지 숨는다).
 */
function EditLink({ post }: { post: PublicPost }) {
  const { status, user } = useAuth();
  if (status !== "authenticated" || !user?.isApproved || !post.author.login || post.author.login !== user.login) return null;
  return (
    <Link
      href={writePostPath(post.postId)}
      className="rounded-lg border border-stone-300 px-3 py-1 text-sm font-medium text-stone-900 hover:bg-stone-100"
    >
      수정<span className="sr-only">: {post.title}</span>
    </Link>
  );
}

/** 글 끝: 목록·태그로 돌아가는 길(#119 E3). */
function ArticleFooter() {
  return (
    <nav aria-label="글 둘러보기" className="mx-auto mt-12 flex max-w-[45rem] flex-wrap gap-x-6 gap-y-2 border-t border-stone-200 pt-6 text-sm font-medium">
      <Link href="/posts/" className="text-indigo-600 hover:text-indigo-800">
        ← 전체 글 목록
      </Link>
      <Link href="/tags/" className="text-indigo-600 hover:text-indigo-800">
        모든 태그
      </Link>
    </nav>
  );
}

function Loaded({ slug }: { slug: string }) {
  const { result } = useLoad(`public-post:${slug}`, () => getPublicPost(slug));
  const { user } = useAuth();
  // 대화창 문맥: 공개 글은 slug가 아니라 postId로 보낸다(#28 계약). 불러오기 전에는 알리지 않는다.
  useRegisterPage(usePathname(), result?.ok ? { kind: "public_post", resourceId: result.data.postId } : null);
  // 서버 HTML에는 글이 없으므로 탭 제목은 불러온 뒤 정한다(그 전에는 metadata의 "글").
  useDocumentTitle(result?.ok ? result.data.title : null);

  if (!result) return <PostArticleSkeleton />;
  if (!result.ok) {
    if (result.failure.kind === "http" && result.failure.status === 404) {
      return (
        <div className="mx-auto max-w-[45rem] space-y-3">
          <Notice tone="info">글을 찾을 수 없습니다. 주소가 잘못되었거나 발행이 취소된 글입니다.</Notice>
          <Link href="/posts/" className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
            전체 글 보기 →
          </Link>
        </div>
      );
    }
    return <ErrorNotice failure={result.failure} prefix="글을 불러오지 못했습니다." />;
  }
  const post = result.data;
  return (
    <>
      <PostArticle
        post={post}
        toc
        viewerIsAuthor={!!user && !!post.author.login && user.login === post.author.login}
        actions={<EditLink post={post} />}
      />
      <ArticleFooter />
    </>
  );
}

export default function PostDetail() {
  const params = useParams<{ slug: string }>();
  if (!API_ENABLED) return <Notice tone="info">{NO_API_POSTS}</Notice>;
  return <Loaded slug={decodeSegment(params.slug)} />;
}
