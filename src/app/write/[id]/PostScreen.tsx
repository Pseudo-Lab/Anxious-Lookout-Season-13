"use client";

import { useParams } from "next/navigation";
import { decodeSegment } from "@/lib/posts/format";
import AuthGate from "@/components/research/AuthGate";
import PostDetail from "./PostDetail";
import WriteFrame from "../WriteFrame";

export default function PostScreen() {
  const id = decodeSegment(useParams<{ id: string }>().id);
  return (
    <WriteFrame>
      <AuthGate>
        <PostDetail key={id} id={id} />
      </AuthGate>
    </WriteFrame>
  );
}
