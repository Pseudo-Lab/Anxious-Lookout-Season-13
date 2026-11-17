// 공통 대화창의 페이지 문맥(#9 api-contracts/issue9-page-chat.md).
// front는 보고 있는 페이지의 종류와 자원 ID만 보낸다. 실제 문맥과 허용 도구는 서버가 소유·공개 상태를 확인해 정하며(#14: 소유자는 화면과 무관),
// 여기 문구는 안내용이다(권한 근거가 아니다).

import { useEffect, useSyncExternalStore } from "react";
import type { PageDescriptor, PageKind } from "@/lib/research/types";
import { own } from "@/lib/research/own";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const detail = (kind: PageKind, id: string | null): PageDescriptor =>
  id && UUID.test(id) ? { kind, resourceId: id.toLowerCase() } : { kind: "general" };

// 주소만으로는 자원 ID를 알 수 없는 화면(#28 공개 글: slug → postId)은 불러온 뒤 등록한다. 같은 경로일 때만 쓴다.
let registered: { path: string; page: PageDescriptor } | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const norm = (p: string) => (p.endsWith("/") ? p : `${p}/`);

/** 이 화면이 보이는 동안 페이지 문맥을 알린다(page가 null이면 알리지 않는다). */
export function useRegisterPage(pathname: string, page: PageDescriptor | null) {
  const kind = page?.kind;
  const resourceId = page?.resourceId;
  useEffect(() => {
    if (!kind) return;
    const mine = { path: norm(pathname), page: { kind, ...(resourceId && { resourceId }) } };
    registered = mine;
    notify();
    return () => {
      if (registered === mine) {
        registered = null;
        notify();
      }
    };
  }, [pathname, kind, resourceId]);
}

export function useRegisteredPage() {
  return useSyncExternalStore(
    subscribe,
    () => registered,
    () => null
  );
}

/** 경로(basePath 제외)와 query로 페이지 종류를 정한다. 알 수 없는 경로·형식은 general(참고할 화면 없음). */
export function describePage(pathname: string, search: URLSearchParams, extra?: { path: string; page: PageDescriptor } | null): PageDescriptor {
  const path = norm(pathname);
  if (extra && extra.path === path) return extra.page;
  // 공개 글(#28)은 slug로는 postId를 알 수 없다. 글을 불러와 등록하기 전(또는 찾지 못한 경우)에는 자원 없는 public_post로 두어
  // 보내기를 잠근다(pageUnresolved). 소유자 범위(general)로 대신 보내지 않는다(리뷰 F2).
  if (/^\/posts\/[^/]+\/$/.test(path)) return { kind: "public_post" };
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
    case "/write/":
      return search.get("id") ? detail("post", search.get("id")) : { kind: "post_workspace" };
    case "/write/new/": {
      const draft = search.get("draft");
      return draft && UUID.test(draft) ? { kind: "post_workspace", draftId: draft.toLowerCase() } : { kind: "post_workspace" };
    }
    case "/research/": {
      // 내 연구 화면의 탭 기본값(자료)과 같은 규칙. #44: 대화 탭이 없어 예전 주소(?tab=sessions)도 자료 탭을 보이므로 자료 화면이다.
      const tab = search.get("tab");
      if (tab === "documents") return { kind: "document_workspace" };
      return { kind: "material_workspace" };
    }
    default:
      return { kind: "general" };
  }
}

/** 범위를 아직 정하지 못한 페이지(공개 글을 불러오는 중이거나 찾지 못함). 새 질문을 보내지 않는다. */
export const pageUnresolved = (page: PageDescriptor) => page.kind === "public_post" && !page.resourceId;

export const sameDescriptor = (a?: PageDescriptor, b?: PageDescriptor) =>
  (a?.kind ?? "general") === (b?.kind ?? "general") && a?.resourceId === b?.resourceId && a?.draftId === b?.draftId;

interface KindText {
  // 페이지 이름(짧게)
  label: string;
  // 이 화면에서 질문하면 할 수 있는 일
  can: string;
  // 실제 도구 경계인지(#14: 공개 글만). 그 밖의 화면은 모델에 "지금 보고 있는 것"을 알리는 참고일 뿐이다.
  bounded: boolean;
}

// #14: 로그인 소유자의 대화는 화면과 무관하게 내 자료·문서·글 도구 전부를 쓴다. 권한 경계는 소유자이며 화면은 참고다.
// 웹 검색과 명령 실행(#23)은 소유자 모드에서만 켜진다. #68 native shell에는 실행기의 한 번에 하나·시간 상한이 없어 적지 않는다.
// #97: 문서·글은 바로 새 버전으로 저장한다. 공개·발행은 사람이 별도로 처리한다.
const OWNER_CAN =
  "내 자료·문서·글 조회·저장과 웹 검색·명령 실행을 할 수 있습니다. 공개·발행은 직접 해야 합니다.";

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
  post_workspace: { label: "글 쓰기", can: OWNER_CAN, bounded: false },
  post: { label: "이 글", can: OWNER_CAN, bounded: false },
  public_post: {
    label: "이 발행 글",
    can: "이 발행 글만 읽고 답하며, 웹 검색·명령 실행이나 저장·수정은 하지 않습니다.",
    bounded: true,
  },
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
