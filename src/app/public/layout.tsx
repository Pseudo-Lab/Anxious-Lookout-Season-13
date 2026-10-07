import type { Metadata } from "next";

export const metadata: Metadata = { title: "공개 문서" };

export default function PublicLayout({ children }: { children: React.ReactNode }) {
  return children;
}
