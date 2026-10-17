import type { Metadata } from "next";

export const metadata: Metadata = { title: "글 쓰기" };

export default function WriteLayout({ children }: { children: React.ReactNode }) {
  return children;
}
