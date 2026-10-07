import { notFound } from "next/navigation";
import { getAllPosts, getPostBySlug } from "@/lib/posts/mdx";
import PostContent from "@/components/posts/PostContent";
import TagBadge from "@/components/posts/TagBadge";
import type { Metadata } from "next";

interface PageProps {
  params: Promise<{ slug: string }>;
}

// 저장소 mdx에서 만든 경로만 제공한다. 빌드에 없는 값은 404이며 런타임 렌더링·캐시 쓰기를 하지 않는다.
export const dynamicParams = false;

export async function generateStaticParams() {
  const posts = getAllPosts();
  return posts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const post = getPostBySlug(slug);
  if (!post) return {};

  return {
    title: post.frontmatter.title,
    description: post.frontmatter.description,
    openGraph: {
      title: post.frontmatter.title,
      description: post.frontmatter.description,
      type: "article",
      publishedTime: post.frontmatter.date,
      authors: [post.frontmatter.author],
      tags: post.frontmatter.tags,
    },
  };
}

export default async function PostPage({ params }: PageProps) {
  const { slug } = await params;
  const post = getPostBySlug(slug);
  if (!post) notFound();

  return (
    <article className="mx-auto max-w-3xl">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight text-stone-900">
          {post.frontmatter.title}
        </h1>
        <p className="mt-2 text-stone-500">
          {post.frontmatter.description}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="text-sm text-stone-500">
            {post.frontmatter.author} &middot; {post.frontmatter.date}
          </span>
          <div className="flex gap-2">
            {post.frontmatter.tags?.map((tag) => (
              <TagBadge key={tag} tag={tag} />
            ))}
          </div>
        </div>
      </header>
      <PostContent content={post.content} />
      <p className="mt-10 border-t border-stone-200 pt-8 text-center text-sm text-stone-500">
        좋아요와 댓글 기능은 새 서버로 옮기는 중입니다(준비 중).
      </p>
    </article>
  );
}
