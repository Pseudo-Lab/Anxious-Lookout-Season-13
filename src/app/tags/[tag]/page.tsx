import TagPosts from "./TagPosts";

// /posts/[slug]와 같은 방식(#28): 태그 목록은 브라우저가 /api/public/posts?tag=로 조회한다.
// standalone은 요청마다 셸만 렌더하고(ƒ) 캐시를 쓰지 않으며, Pages export는 자리표시 1개만 생성한다.
const EXPORT = process.env.NEXT_OUTPUT === "export";

export const generateStaticParams = EXPORT ? () => [{ tag: "_" }] : undefined;

export default function TagPage() {
  return <TagPosts />;
}
