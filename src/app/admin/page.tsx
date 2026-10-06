import type { Metadata } from "next";
import ComingSoon from "@/components/layout/ComingSoon";

export const metadata: Metadata = { title: "관리" };

export default function AdminPage() {
  return (
    <ComingSoon
      title="관리"
      description="회원 승인·역할 변경, 글·댓글 관리 화면을 준비하고 있습니다."
    />
  );
}
