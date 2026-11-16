import { BASE_PATH } from "@/lib/constants";

// api-contracts/issue97-agent-research-v1.md: 버전의 작성 주체. 글 상위 author.login(소유자)과 별개다.
export interface VersionAuthor {
  kind: "human" | "codex" | "unknown";
  conversationId: string | null;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 추가형 메타이므로 누락/미래 형식이 버전 본문 조회를 막지 않는다. 누락만 기존 사람 버전이다. */
export function parseVersionAuthor(value: unknown): VersionAuthor {
  if (value === undefined) return { kind: "human", conversationId: null };
  if (typeof value !== "object" || value === null || Array.isArray(value)) return { kind: "unknown", conversationId: null };
  const v = value as Record<string, unknown>;
  if (v.kind === "human") return { kind: "human", conversationId: null };
  if (v.kind === "codex") return {
    kind: "codex",
    conversationId: typeof v.conversationId === "string" && UUID.test(v.conversationId) ? v.conversationId.toLowerCase() : null,
  };
  return { kind: "unknown", conversationId: null };
}

export function versionAuthorLabel(author?: VersionAuthor): string {
  return author?.kind === "codex" ? "Codex" : !author || author.kind === "human" ? "사람" : "작성자 미상";
}

export function versionConversationHref(author?: VersionAuthor): string | null {
  if (author?.kind !== "codex" || !author.conversationId || !UUID.test(author.conversationId)) return null;
  return `${BASE_PATH}/research/session/?${new URLSearchParams({ id: author.conversationId.toLowerCase() })}`;
}
