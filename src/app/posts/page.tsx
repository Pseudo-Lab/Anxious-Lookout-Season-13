import type { Metadata } from "next";
import Link from "next/link";
import PostBrowser from "@/components/posts/PostBrowser";

export const metadata: Metadata = {
  title: "전체 글",
};

export default function AllPostsPage() {
  return (
    <>
      <div className="mb-8 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-2xl font-bold text-stone-900">전체 글</h1>
        {/* #119 E3 */}
        <Link href="/tags/" className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          모든 태그 보기 →
        </Link>
      </div>
      <PostBrowser />
    </>
  );
}
