import type { Metadata } from "next";

// 개인 자료 화면. 검색 노출 대상이 아니다.
export const metadata: Metadata = {
  title: "내 연구",
  robots: { index: false, follow: false },
};

export default function ResearchLayout({ children }: { children: React.ReactNode }) {
  return children;
}
