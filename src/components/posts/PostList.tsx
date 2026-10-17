import type { PublicPost } from "@/lib/posts/types";
import PostCard from "./PostCard";

export function EmptyPosts({ text = "아직 게시된 글이 없습니다" }: { text?: string }) {
  return (
    <div className="rounded-2xl bg-white px-6 py-20 text-center shadow-sm ring-1 ring-stone-200/60">
      <svg className="mx-auto h-16 w-16 text-stone-300" fill="none" stroke="currentColor" strokeWidth={1} viewBox="0 0 24 24">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
        />
      </svg>
      <p className="mt-4 text-lg font-medium text-stone-500">{text}</p>
    </div>
  );
}

export default function PostList({ posts }: { posts: PublicPost[] }) {
  if (posts.length === 0) return <EmptyPosts />;
  return (
    <div className="flex flex-col gap-4">
      {posts.map((post) => (
        <PostCard key={post.id} post={post} />
      ))}
    </div>
  );
}
