// api-contracts/m3-research-data.md 기준 응답 형태.

export type ItemType = "material" | "document";
export type ContentKind = "full" | "excerpt" | "summary";

export const CONTENT_KINDS: readonly ContentKind[] = ["full", "excerpt", "summary"];

export const CONTENT_KIND_LABELS: Record<ContentKind, string> = {
  full: "전체",
  excerpt: "발췌",
  summary: "요약",
};

export const ITEM_TYPE_LABELS: Record<ItemType, string> = {
  material: "자료",
  document: "문서",
};

export interface VersionRef {
  id: string;
  number: number;
  createdAt: string;
}

export interface Publication {
  id: string;
  state: "published";
  versionId: string;
  publishedAt: string;
  // 자료 item ID
  materialIds: string[];
  // 공개에 고정된 자료 버전(선택 편집용)
  materials: { id: string; versionId: string }[];
}

interface ItemBase {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  // 낙관적 동시성 revision(보관·공개 상태 변경 포함). 내용 버전 번호는 latestVersion.number.
  version: number;
  latestVersion: VersionRef;
  archived: boolean;
}

export interface MaterialSummary extends ItemBase {
  type: "material";
  sourceUrl: string;
  collectedAt: string;
  contentKind: ContentKind;
}

export interface Material extends MaterialSummary {
  content: string;
}

export interface DocumentSummary extends ItemBase {
  type: "document";
  publication: Publication | null;
}

export interface ResearchDocument extends DocumentSummary {
  content: string;
}

export interface VersionSummary {
  id: string;
  itemId: string;
  number: number;
  title: string;
  createdAt: string;
  // 자료 스냅샷에만 있음
  sourceUrl?: string;
  collectedAt?: string;
  contentKind?: ContentKind;
}

export interface Version extends VersionSummary {
  content: string;
}

export interface Endpoint {
  type: ItemType;
  id: string;
  title: string;
}

export type RelationDirection = "outgoing" | "incoming" | "bidirectional";

export interface Relation {
  id: string;
  source: Endpoint;
  target: Endpoint;
  kind: string;
  description: string;
  directed: boolean;
  version: number;
  createdAt: string;
  updatedAt: string;
  // 대상별 관계 목록에서만 있음(이 항목 기준 방향)
  direction?: RelationDirection;
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export interface PublishedMaterial {
  id: string;
  versionId: string;
  title: string;
  sourceUrl: string;
  collectedAt: string;
  contentKind: ContentKind;
  // 공개 노출 범위가 미정이다. 메타데이터만 공개하는 정책이면 null.
  content: string | null;
}

export interface PublicSnapshot {
  id: string;
  documentId: string;
  versionId: string;
  title: string;
  content: string;
  publishedAt: string;
  materials: PublishedMaterial[];
  author: { login: string };
}

export interface PublicDocumentSummary {
  id: string;
  documentId: string;
  title: string;
  publishedAt: string;
  author: { login: string };
}

export interface ArchiveResult {
  id: string;
  archived: true;
  version: number;
}

export interface CodexStatus {
  available: boolean;
  reason: null | "not_configured" | "unavailable";
  verification: "unverified" | "fixture" | "real";
}

export type SessionState = "idle" | "running" | "failed";

export interface SessionSummary {
  id: string;
  title: string;
  state: SessionState;
  createdAt: string;
  updatedAt: string;
  version: number;
}

export interface MessageItem {
  id: string;
  type: "message";
  role: "user" | "assistant";
  text: string;
  // Codex 원본에 아직/끝내 기록되지 않은 사용자 입력(플랫폼이 보존). 모델이 본 이력이 아니다.
  source?: "platform";
  status?: "pending" | "not_recorded";
}

export type SessionItem =
  | MessageItem
  | { id: string; type: "tool_call"; name: string; input: unknown; output: unknown; status: string };

export interface SessionDetail extends SessionSummary {
  items: SessionItem[];
  error: null | { code: string; message: string };
}

// 계약의 길이 제한
export const LIMITS = {
  title: 300,
  content: 200_000,
  description: 4_000,
  kind: 80,
} as const;

// 세션 항목의 저장된 원본 전체(도구 입력·출력 포함). 실행하지 않고 JSON 텍스트로만 표시한다.
export interface RawSessionItem {
  id: string;
  format: "json";
  raw: unknown;
}
