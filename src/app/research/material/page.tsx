import Link from "next/link";
import LegacyIdRedirect from "@/components/layout/LegacyIdRedirect";
import Notice from "@/components/research/Notice";

// #119 B2: 예전 주소 /research/material/?id=<id> → /research/material/<id>/
export default function LegacyMaterialPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <LegacyIdRedirect
        route="material"
        fallback={
          <Notice tone="error">
            자료 ID가 없습니다. <Link href="/research/?tab=materials" className="underline">자료 목록으로</Link>
          </Notice>
        }
      />
    </div>
  );
}
