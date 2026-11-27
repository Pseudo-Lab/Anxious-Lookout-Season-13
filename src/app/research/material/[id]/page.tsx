import type { Metadata } from "next";
import MaterialScreen from "./MaterialScreen";

// #119 B2: 경로형 상세 주소. 브라우저가 API로 내용을 읽는다(서버는 셸만, 캐시 없음).
// GitHub Pages export는 동적 경로에 자리표시 1개만 만든다(API가 없어 내용도 없다).
const EXPORT = process.env.NEXT_OUTPUT === "export";

export const generateStaticParams = EXPORT ? () => [{ id: "_" }] : undefined;

// #119 A3: 불러오기 전에도 "자료"로 구분되고, 불러오면 제목으로 바뀐다.
export const metadata: Metadata = { title: "자료" };

export default function Page() {
  return <MaterialScreen />;
}
