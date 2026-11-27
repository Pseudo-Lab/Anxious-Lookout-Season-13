"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { API_ENABLED } from "@/lib/constants";
import { useAuth } from "@/hooks/useAuth";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { getPost, getPublicPost, getRelatedPosts, type PostRef } from "@/lib/posts/api";
import { decodeSegment, formatDate } from "@/lib/posts/format";
import type { FetchResult } from "@/lib/api/client";
import type { Post, PublicPost } from "@/lib/posts/types";
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

function RefLink({ post, label }: { post: PostRef; label?: string }) {
  return (
    <Link href={`/posts/${encodeURIComponent(post.slug)}/`} className="group block rounded-lg p-3 ring-1 ring-stone-200 hover:bg-stone-50 hover:ring-indigo-200">
      {label && <span className="block text-xs text-stone-600">{label}</span>}
      <span className="mt-0.5 block font-medium text-stone-900 group-hover:text-indigo-700">{post.title || "(제목 없음)"}</span>
      <span className="mt-0.5 block text-xs text-stone-600">
        {post.author.name || post.author.login} · <time dateTime={post.publishedAt}>{formatDate(post.publishedAt)}</time>
      </span>
    </Link>
  );
}

/** #119 F2: 이전·다음 글, 같은 태그의 글. 불러오지 못하면 목록 링크만 남는다(본문 읽기를 막지 않는다). */
function Related({ slug }: { slug: string }) {
  const { result } = useLoad(`public-post-related:${slug}`, () => getRelatedPosts(slug));
  if (!result?.ok) return null;
  const { previous, next, sameTag } = result.data;
  return (
    <>
      {(previous || next) && (
        <nav aria-label="이전 글과 다음 글" className="grid gap-3 sm:grid-cols-2">
          <div>{previous && <RefLink post={previous} label="← 이전 글" />}</div>
          <div className="sm:text-right">{next && <RefLink post={next} label="다음 글 →" />}</div>
        </nav>
      )}
      {sameTag.length > 0 && (
        <section aria-labelledby="same-tag-posts">
          <h2 id="same-tag-posts" className="text-sm font-semibold text-stone-900">
            같은 태그의 글
          </h2>
          <ul className="mt-2 space-y-2">
            {sameTag.map((p) => (
              <li key={p.slug}>
                <RefLink post={p} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** 글 끝: 이전·다음·같은 태그(F2), 목록·태그로 돌아가는 길(#119 E3). */
function ArticleFooter({ slug }: { slug: string }) {
  return (
    <div className="mx-auto mt-12 max-w-[45rem] space-y-6 border-t border-stone-200 pt-6">
      <Related slug={slug} />
      <nav aria-label="글 둘러보기" className="flex flex-wrap gap-x-6 gap-y-2 text-sm font-medium">
        <Link href="/posts/" className="text-indigo-600 hover:text-indigo-800">
          ← 전체 글 목록
        </Link>
        <Link href="/tags/" className="text-indigo-600 hover:text-indigo-800">
          모든 태그
        </Link>
      </nav>
    </div>
  );
}

/**
 * #119 F4(issue119 §F4): 공개 응답에는 지금 공개된 바탕 문서만 온다. 작성자에게는 본인 글 조회의
 * publication.documents(비공개 포함)로 "공개하면 링크됩니다" 안내를 함께 보인다. 읽지 못하면 공개 응답 그대로.
 */
function useAuthorDocuments(post: PublicPost | null, isAuthor: boolean): PublicPost["documents"] | null {
  const key = isAuthor && post ? `post:${post.postId}` : null;
  const { result } = useLoad(key ?? "author-docs:none", () =>
    post && isAuthor ? getPost(post.postId) : Promise.resolve<FetchResult<Post>>({ ok: false, failure: { kind: "network" } })
  );
  if (!isAuthor || !result?.ok) return null;
  const docs = result.data.publication?.documents;
  if (!docs || result.data.publication?.versionId !== post?.versionId) return null;
  return docs.map((d) => ({ id: d.id, title: d.title, publicUrl: d.public ? d.publicUrl : null }));
}

function Loaded({ slug }: { slug: string }) {
  const { result } = useLoad(`public-post:${slug}`, () => getPublicPost(slug));
  const { user } = useAuth();
  const loaded = result?.ok ? result.data : null;
  const isAuthor = !!user && !!loaded?.author.login && user.login === loaded.author.login;
  const authorDocs = useAuthorDocuments(loaded, isAuthor);
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
        post={authorDocs ? { ...post, documents: authorDocs } : post}
        toc
        viewerIsAuthor={isAuthor}
        actions={<EditLink post={post} />}
      />
      <ArticleFooter slug={slug} />
    </>
  );
}

export default function PostDetail() {
  const params = useParams<{ slug: string }>();
  if (!API_ENABLED) return <Notice tone="info">{NO_API_POSTS}</Notice>;
  return <Loaded slug={decodeSegment(params.slug)} />;
}
