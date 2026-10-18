import type { FetchFailure } from "@/lib/api/client";
import { researchErrorMessage } from "@/lib/research/api";
import { own } from "@/lib/research/own";

// #28 글 화면의 오류 안내. 글 고유 사유를 먼저 보고, 나머지는 research 공통 문구를 쓴다.
const POST_REASONS: Record<string, string> = {
  active_draft_exists: "이 글에는 이미 작성 중인 초안이 있습니다. 그 초안을 불러와 이어서 쓰세요.",
  post_version: "글이 그사이 바뀌었습니다(다른 저장·발행 등). 현재 상태를 다시 불러온 뒤 시도하세요.",
  base_changed: "초안을 시작한 뒤 글이 바뀌었습니다. 현재 버전과 비교한 뒤 다시 저장하세요.",
  archived: "보관한 글이라 저장·수락·발행할 수 없습니다. 입력은 그대로 있습니다.",
  slug_taken: "공개 주소를 정하지 못했습니다. 잠시 뒤 다시 저장하세요.",
  preview_changed: "미리보기 뒤에 글·연결 문서·이미지 상태가 바뀌었습니다. 미리보기를 다시 확인한 뒤 발행하세요.",
  image_in_use: "초안·제안·버전·발행본 중 어딘가에서 쓰고 있는 이미지라 지울 수 없습니다.",
};

export function postErrorMessage(failure: FetchFailure): string | null {
  if (failure.kind === "http") {
    if (failure.code === "conflict") {
      const text = own(POST_REASONS, failure.reason ?? "");
      if (text) return text;
    }
    if (failure.code === "payload_too_large") return "이미지가 너무 큽니다. 5 MiB 이하 파일만 올릴 수 있습니다.";
    if (failure.code === "storage_quota_exceeded") return "계정의 이미지 저장 공간(100 MiB)을 다 썼습니다. 쓰지 않는 이미지를 지운 뒤 다시 올리세요.";
    if (failure.code === "unsupported_media_type") return "PNG, JPEG, WebP 이미지만 올릴 수 있습니다.";
  }
  return researchErrorMessage(failure);
}
