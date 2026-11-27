import Link from "next/link";
import LegacyIdRedirect from "@/components/layout/LegacyIdRedirect";
import Notice from "@/components/research/Notice";

// #119 B2: 예전 주소 /research/document/?id=<id> → /research/document/<id>/
export default function LegacyDocumentPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <LegacyIdRedirect
        route="document"
        fallback={
          <Notice tone="error">
            문서 ID가 없습니다. <Link href="/research/?tab=documents" className="underline">문서 목록으로</Link>
          </Notice>
        }
      />
    </div>
  );
}
