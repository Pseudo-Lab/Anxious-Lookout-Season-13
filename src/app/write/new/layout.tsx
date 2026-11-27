import type { Metadata } from "next";

// #119 A3: 탭 제목이 다른 화면과 구분되게
export const metadata: Metadata = { title: "새 글" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
