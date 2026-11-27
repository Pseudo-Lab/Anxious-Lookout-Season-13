import type { Metadata } from "next";
import { decodeSegment } from "@/lib/posts/format";
import TagPosts from "./TagPosts";

// /posts/[slug]와 같은 방식(#28): 태그 목록은 브라우저가 /api/public/posts?tag=로 조회한다.
// standalone은 요청마다 셸만 렌더하고(ƒ) 캐시를 쓰지 않으며, Pages export는 자리표시 1개만 생성한다.
const EXPORT = process.env.NEXT_OUTPUT === "export";

export const generateStaticParams = EXPORT ? () => [{ tag: "_" }] : undefined;

// #119 A3: 탭 제목 "# 태그명". 주소만으로 정해지므로 서버가 바로 넣는다(API 호출 없음).
export async function generateMetadata({ params }: { params: Promise<{ tag: string }> }): Promise<Metadata> {
  const { tag } = await params;
  return { title: `# ${decodeSegment(tag)}` };
}

export default function TagPage() {
  return <TagPosts />;
}
