"use client";

import { Suspense, useEffect, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { API_ENABLED } from "@/lib/constants";
import { DETAIL_PATH, type DetailRoute } from "@/lib/routes";
import Notice from "@/components/research/Notice";

// API가 없는 정적 배포(GitHub Pages)에는 경로형 상세 화면이 자리표시 하나뿐이다. 넘기지 않고 같은 안내만 보인다.
const NO_API: Record<DetailRoute, string> = {
  material: "이 정적 사이트에는 API 서버가 없어 개인 자료 기능을 제공하지 않습니다.",
  document: "이 정적 사이트에는 API 서버가 없어 개인 자료 기능을 제공하지 않습니다.",
  post: "이 정적 사이트에는 API 서버가 없어 개인 자료 기능을 제공하지 않습니다.",
  public_document: "이 정적 사이트에는 API 서버가 없어 공개 문서를 제공하지 않습니다.",
};

/**
 * #119 B2(D2): 예전 쿼리 주소(`…/?id=<id>`)를 경로형 새 주소로 넘긴다. 공유된 링크·서버가 만든 publicUrl·대화 기록의 링크가
 * 계속 열리게 한다. id 말고 다른 검색어와 #조각(예: #version-history)은 그대로 옮긴다. 기록에는 남기지 않는다(replace).
 */
function Redirect({ route, fallback }: { route: DetailRoute; fallback: ReactNode }) {
  const params = useSearchParams();
  const router = useRouter();
  const id = params.get("id");
  useEffect(() => {
    if (!id || !API_ENABLED) return;
    const rest = new URLSearchParams(params.toString());
    rest.delete("id");
    const query = rest.toString();
    router.replace(`${DETAIL_PATH[route](id)}${query ? `?${query}` : ""}${window.location.hash}`);
  }, [id, params, router, route]);
  if (!API_ENABLED) return <Notice tone="info">{NO_API[route]}</Notice>;
  if (!id) return <>{fallback}</>;
  return (
    <p role="status" className="text-sm text-stone-500">
      새 주소로 이동하는 중...
    </p>
  );
}

export default function LegacyIdRedirect({ route, fallback }: { route: DetailRoute; fallback: ReactNode }) {
  return (
    <Suspense>
      <Redirect route={route} fallback={fallback} />
    </Suspense>
  );
}
