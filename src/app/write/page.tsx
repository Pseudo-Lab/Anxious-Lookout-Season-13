import LegacyIdRedirect from "@/components/layout/LegacyIdRedirect";
import AuthGate from "@/components/research/AuthGate";
import PostIndex from "./PostIndex";
import WriteFrame from "./WriteFrame";

// #28 글 쓰기 목록(/write/). 새 글은 /write/new/(?draft=), 글 화면은 /write/<id>/(#119 B2).
// 예전 글 화면 주소(/write/?id=<id>)는 다른 상세 주소와 같은 규칙으로 넘긴다(다른 검색어·#조각 유지).
export default function WritePage() {
  return (
    <WriteFrame>
      <AuthGate>
        <LegacyIdRedirect route="post" fallback={<PostIndex />} />
      </AuthGate>
    </WriteFrame>
  );
}
