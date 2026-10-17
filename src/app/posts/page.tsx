import type { Metadata } from "next";
import PublicPostFeed from "@/components/posts/PublicPostFeed";

export const metadata: Metadata = {
  title: "전체 글",
};

export default function AllPostsPage() {
  return (
    <>
      <h1 className="mb-8 text-2xl font-bold text-stone-900">전체 글</h1>
      <PublicPostFeed />
    </>
  );
}
