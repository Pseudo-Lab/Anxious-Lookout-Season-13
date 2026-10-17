import PostDetail from "./PostDetail";

// #28: 발행본은 DB에 있고 브라우저가 /api/public/posts/{slug}로 조회한다(서버는 API를 부르지 않는다).
// k3s standalone: generateStaticParams가 없어 요청마다 셸만 렌더하고(ƒ) 캐시를 쓰지 않는다(읽기 전용 FS, 발행 취소 즉시 반영).
// GitHub Pages export: 동적 경로에는 generateStaticParams가 필요해 자리표시 1개만 생성한다(API가 없어 글도 없다).
const EXPORT = process.env.NEXT_OUTPUT === "export";

export const generateStaticParams = EXPORT ? () => [{ slug: "_" }] : undefined;

export default function PostPage() {
  return <PostDetail />;
}
