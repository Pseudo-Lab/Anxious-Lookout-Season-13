import type { Metadata } from "next";
import TagCloud from "./TagCloud";

export const metadata: Metadata = {
  title: "태그",
  description: "모든 태그 목록",
};

export default function TagsPage() {
  return (
    <>
      <h1 className="mb-8 text-3xl font-bold tracking-tight">태그</h1>
      <TagCloud />
    </>
  );
}
