// 공통 대화창의 페이지 문맥(#9 api-contracts/issue9-page-chat.md).
// front는 보고 있는 페이지의 종류와 자원 ID만 보낸다. 실제 문맥과 허용 도구는 서버가 소유·공개 상태를 확인해 정하며,
// 여기 문구는 안내용이다(권한 근거가 아니다).

import type { PageDescriptor, PageKind } from "@/lib/research/types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const detail = (kind: PageKind, id: string | null): PageDescriptor =>
  id && UUID.test(id) ? { kind, resourceId: id.toLowerCase() } : { kind: "general" };

/** 경로(basePath 제외)와 query로 페이지 종류를 정한다. 알 수 없는 경로·형식은 general(도구 없음). */
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
  // 이 페이지에서 질문하면 할 수 있는 일
  can: string;
}

const KIND_TEXT: Record<string, KindText> = {
  general: {
    label: "일반 질문",
    can: "자료·문서 도구 없이 질문에 답합니다.",
  },
  public_document: {
    label: "이 공개 글",
    can: "이 공개 글과 직접 참고자료를 읽고 답합니다.",
  },
  material: {
    label: "이 자료",
    can: "이 자료와 버전·연결 관계를 읽고, 요청하면 이 자료를 새 버전으로 수정합니다.",
  },
  document: {
    label: "이 문서",
    can: "이 문서와 버전·연결 관계를 읽고, 요청하면 이 문서를 새 버전으로 수정합니다.",
  },
  material_workspace: {
    label: "내 자료",
    can: "내 자료 목록을 보고, 요청하면 새 자료로 저장합니다.",
  },
  document_workspace: {
    label: "내 문서",
    can: "내 문서 목록을 보고, 요청하면 새 문서로 저장합니다.",
  },
};

export function kindText(kind: string): KindText {
  return KIND_TEXT[kind] ?? { label: "알 수 없는 범위", can: "서버가 정한 범위로 처리되었습니다." };
}

// 이 페이지에서 할 수 없는 일의 안내: 관계 연결·보관은 대화창에서 하지 않고 각 화면에서 직접 한다.
export function unsupportedHint(kind: string): string {
  switch (kind) {
    case "general":
    case "public_document":
      return "내 자료·문서를 읽거나 저장하려면 내 연구에서 해당 목록이나 항목을 연 채로 질문하세요.";
    case "material":
    case "document":
      return "연결 관계 추가·변경과 보관은 이 화면에서 직접 할 수 있습니다.";
    default:
      return "기존 항목 수정은 해당 항목을 연 채로 질문하고, 연결 관계와 보관은 각 항목 화면에서 직접 할 수 있습니다.";
  }
}
