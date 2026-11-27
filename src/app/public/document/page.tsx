import Link from "next/link";
import LegacyIdRedirect from "@/components/layout/LegacyIdRedirect";
import Notice from "@/components/research/Notice";

// #119 B2: 예전 주소 /public/document/?id=<id>(서버 publicUrl 형식 포함) → /public/document/<id>/
export default function LegacyPublicDocumentPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <LegacyIdRedirect
        route="public_document"
        fallback={
          <Notice tone="error">
            문서 ID가 없습니다. <Link href="/public/" className="underline">공개 문서 목록으로</Link>
          </Notice>
        }
      />
    </div>
  );
}
