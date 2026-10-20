import Link from "next/link";
import Hero from "@/components/layout/Hero";
import PublicPostFeed from "@/components/posts/PublicPostFeed";

export default function HomePage() {
  return (
    <>
      <Hero />

      <section className="mb-12">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-stone-900">최근 글</h2>
          <Link
            href="/posts/"
            className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
          >
            전체 보기 →
          </Link>
        </div>
        <PublicPostFeed limit={5} more={false} />
      </section>
    </>
  );
}
