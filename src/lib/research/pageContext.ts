// 공통 대화창의 페이지 문맥(#9 api-contracts/issue9-page-chat.md).
// front는 보고 있는 페이지의 종류와 자원 ID만 보낸다. 실제 문맥과 허용 도구는 서버가 소유·공개 상태를 확인해 정하며(#14: 소유자는 화면과 무관),
// 여기 문구는 안내용이다(권한 근거가 아니다).

import type { PageDescriptor, PageKind } from "@/lib/research/types";
import { own } from "@/lib/research/own";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const detail = (kind: PageKind, id: string | null): PageDescriptor =>
  id && UUID.test(id) ? { kind, resourceId: id.toLowerCase() } : { kind: "general" };

/** 경로(basePath 제외)와 query로 페이지 종류를 정한다. 알 수 없는 경로·형식은 general(참고할 화면 없음). */
export function describePage(pathname: string, search: URLSearchParams): PageDescriptor {
  const path = pathname.endsWith("/") ? pathname : `${pathname}/`;
  switch (path) {
    case "/public/document/":
      return detail("public_document", search.get("id"));
    case "/research/material/":
      return detail("material", search.get("id"));
    case "/research/document/":
      return detail("document", search.get("id"));
    case "/research/material/new/":
      return { kind: "material_workspace" };
    case "/research/document/new/":
      return { kind: "document_workspace" };
    case "/research/": {
      // 내 연구 화면의 탭 기본값(자료)과 같은 규칙
      const tab = search.get("tab");
      if (tab === "documents") return { kind: "document_workspace" };
      if (tab === "sessions") return { kind: "general" };
      return { kind: "material_workspace" };
    }
    default:
      return { kind: "general" };
  }
}

export const sameDescriptor = (a?: PageDescriptor, b?: PageDescriptor) =>
  (a?.kind ?? "general") === (b?.kind ?? "general") && a?.resourceId === b?.resourceId;

interface KindText {
  // 페이지 이름(짧게)
  label: string;
  // 이 화면에서 질문하면 할 수 있는 일
  can: string;
  // 실제 도구 경계인지(#14: 공개 글만). 그 밖의 화면은 모델에 "지금 보고 있는 것"을 알리는 참고일 뿐이다.
  bounded: boolean;
}

// #14: 로그인 소유자의 대화는 화면과 무관하게 내 자료·문서 도구 전부를 쓴다. 권한 경계는 소유자이며 화면은 참고다.
// 웹 검색과 명령 실행(#23)은 소유자 모드에서만 켜진다. 명령 시간 상한은 운영 설정이라 숫자를 적지 않는다.
const OWNER_CAN =
  "내 자료·문서를 찾아 읽고, 요청하면 새로 저장하거나 새 버전으로 고치고, 연결 관계와 보관도 처리합니다. 필요하면 웹을 검색하고, 찾은 내용은 출처 주소와 수집 시각을 붙여 자료로 저장합니다. 내 작업 공간에서 명령을 실행해 파일을 다루거나 계산할 수 있으며, 명령은 한 번에 하나씩 시간 상한 안에서 실행됩니다.";

const KIND_TEXT: Record<string, KindText> = {
  general: { label: "일반", can: OWNER_CAN, bounded: false },
  public_document: {
    label: "이 공개 글",
    can: "이 공개 글과 직접 참고자료만 읽고 답하며, 웹 검색·명령 실행이나 저장·수정은 하지 않습니다.",
    bounded: true,
  },
  material: { label: "이 자료", can: OWNER_CAN, bounded: false },
  document: { label: "이 문서", can: OWNER_CAN, bounded: false },
  material_workspace: { label: "내 자료", can: OWNER_CAN, bounded: false },
  document_workspace: { label: "내 문서", can: OWNER_CAN, bounded: false },
};

export function kindText(kind: string): KindText {
  return own(KIND_TEXT, kind) ?? { label: "알 수 없는 화면", can: "서버가 정한 범위로 처리되었습니다.", bounded: false };
}

/** 범위 표시 문구. 공개 글은 실제 경계라 "범위", 나머지는 참고로 전달한 "화면"이다. */
export function scopeLabel(kind: string, when: "next" | "sent"): string {
  const text = kindText(kind);
  if (text.bounded) return `${when === "next" ? "다음 질문 범위" : "보낸 범위"}: ${text.label}`;
  return `${when === "next" ? "보고 있는 화면" : "보낸 화면"}: ${text.label}`;
}
