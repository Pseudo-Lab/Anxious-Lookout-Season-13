import type { Metadata } from "next";
import ComingSoon from "@/components/layout/ComingSoon";

export const metadata: Metadata = { title: "글 보기" };

// 웹에서 작성해 저장하던 글(/view/?id=)의 보기 화면. 저장소를 새 API로 옮긴 뒤 다시 제공한다.
export default function ViewPage() {
  return (
    <ComingSoon
      title="작성한 글 보기"
      description="웹에서 작성한 글을 불러오는 기능을 준비하고 있습니다. 저장소에 포함된 글은 글 목록에서 볼 수 있습니다."
    />
  );
}
