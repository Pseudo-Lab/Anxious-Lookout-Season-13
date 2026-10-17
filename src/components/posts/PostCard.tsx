import Link from "next/link";
import type { PublicPost } from "@/lib/posts/types";
import { isOwnImage } from "@/components/research/SafeMarkdown";
import { formatDate } from "@/lib/posts/format";
import TagBadge from "./TagBadge";

export default function PostCard({ post }: { post: PublicPost }) {
  const cover = post.coverImageUrl && isOwnImage(post.coverImageUrl) ? post.coverImageUrl : null;
  return (
    <article className="group rounded-xl bg-white p-6 shadow-sm ring-1 ring-stone-200/60 transition-all hover:shadow-md hover:ring-indigo-200">
      <Link href={`/posts/${encodeURIComponent(post.slug)}/`} className="flex gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-semibold tracking-tight text-stone-900 transition-colors group-hover:text-indigo-600">
            {post.title}
          </h2>
          {post.description && <p className="mt-2 line-clamp-2 text-stone-500">{post.description}</p>}
        </div>
        {cover && (
          // eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음)
          <img src={cover} alt="" loading="lazy" className="h-20 w-28 shrink-0 rounded-lg object-cover ring-1 ring-stone-200" />
        )}
      </Link>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="text-sm text-stone-400">
          {post.author.name ? `${post.author.name} · ` : ""}
          {formatDate(post.publishedAt)}
        </span>
        <div className="flex flex-wrap gap-2">
          {post.tags.map((tag) => (
            <TagBadge key={tag} tag={tag} />
          ))}
        </div>
      </div>
    </article>
  );
}
