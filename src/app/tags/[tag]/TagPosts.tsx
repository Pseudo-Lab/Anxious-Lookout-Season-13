"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import { SITE_NAME } from "@/lib/constants";
import { decodeSegment } from "@/lib/posts/format";
import PublicPostFeed from "@/components/posts/PublicPostFeed";

export default function TagPosts() {
  const params = useParams<{ tag: string }>();
  const tag = decodeSegment(params.tag);
  useEffect(() => {
    document.title = `"${tag}" 태그 글 목록 | ${SITE_NAME}`;
  }, [tag]);
  return (
    <>
      <h1 className="mb-8 text-3xl font-bold tracking-tight">
        <span className="text-stone-400">#</span> {tag}
      </h1>
      <PublicPostFeed tag={tag} />
    </>
  );
}
