import type { Metadata } from "next";
import ComingSoon from "@/components/layout/ComingSoon";

export const metadata: Metadata = { title: "글 쓰기" };

export default function WritePage() {
  return (
    <ComingSoon
      title="글 쓰기"
      description="글 작성·임시 저장·발행과 이미지 업로드 기능을 준비하고 있습니다."
    />
  );
}
