import Link from "next/link";
import Hero from "@/components/layout/Hero";
import PostList from "@/components/posts/PostList";
import { getAllPosts } from "@/lib/posts/mdx";

export default function HomePage() {
  const recentPosts = getAllPosts().slice(0, 5);

  return (
    <>
      <Hero />

      <section className="mb-12">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-stone-800">최근 글</h2>
          <Link
            href="/posts/"
            className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
          >
            전체 보기 →
          </Link>
        </div>
        <PostList posts={recentPosts} />
      </section>
    </>
  );
}
