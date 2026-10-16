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
  // 공개에 고정된 자료 내용 버전 번호
  number?: number;
  title: string;
  sourceUrl: string;
  collectedAt: string;
  contentKind: ContentKind;
  // 확정 정책: 공개 자료는 저장 내용과 출처를 항상 포함한다.
  content: string;
}

/** 공개 전 미리보기: 지금 공개하면 함께 노출될 직접 연결 자료(서버 도출). */
export interface PreviewMaterial {
  id: string;
  versionId: string;
  number: number;
  title: string;
  sourceUrl: string;
  collectedAt: string;
  contentKind: ContentKind;
  content: string;
  // 보관된 자료는 미리보기에 보이지만 관계를 해제하기 전까지 공개를 막는다.
  archived: boolean;
  relations: { id: string; version: number }[];
}

export interface PublicationPreview {
  documentId: string;
  versionId: string;
  title: string;
  content: string;
  expectedVersion: number;
  previewToken: string;
  publishable: boolean;
  materials: PreviewMaterial[];
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
  // 서버가 정한 사유 코드. 새 코드가 추가될 수 있으므로 모르는 값은 일반 "사용할 수 없음"으로 표시한다.
  reason: string | null;
  verification: "unverified" | "fixture" | "real";
  // 서버가 강제하는 모델(표시 전용). 연결·모델 표시만으로 실제 응답 성공을 뜻하지 않는다.
  model?: string;
  // 이 계정의 Codex 접수가 끝나는 시각(서버가 접근 만료보다 이르게 잡은 시험 창 끝). 표시용이며 판정은 서버가 한다.
  accessExpiresAt?: string;
  // 현재 시험 창에서 남은 접수 횟수.
  remainingAdmissions?: number;
  // #10: 일시 상태(환경 활성화 중·사용 대기·연결 확인)에서 다시 조회할 간격 안내(초, 1–60). 준비·전송 지시가 아니다.
  retryAfterSeconds?: number;
}

export type SessionState = "idle" | "running" | "failed";

// 질문을 보낸 페이지(#9 api-contracts/issue9-page-chat.md). front는 kind·resourceId만 보내고
// 문맥·허용 도구는 서버가 자원 소유·공개 상태를 확인해 요청마다 정한다.
export type PageKind = "general" | "public_document" | "material" | "document" | "material_workspace" | "document_workspace";

export interface PageDescriptor {
  kind: PageKind;
  resourceId?: string;
}

// 서버가 접수한 요청의 적용 범위(표시용). 새 값이 생길 수 있으므로 kind·capability는 문자열로 받고 모르는 값은 일반 문구로 표시한다.
export interface AcceptedScope {
  page: { kind: string; resourceId?: string; title?: string };
  capability: string;
  allowedTools: string[];
}

export interface SessionSummary {
  id: string;
  title: string;
  state: SessionState;
  createdAt: string;
  updatedAt: string;
  version: number;
  // 공개 글에서 시작한 질문 세션의 처음 저장 맥락(당시 공개본). 이후 질문의 문맥·권한에는 쓰이지 않는 생성 기록이다.
  context?: { documentId: string; publicationId: string; title: string };
  // 가장 최근에 접수된 요청의 범위. 이전 기록에는 없을 수 있다.
  scope?: AcceptedScope;
}

export interface MessageItem {
  id: string;
  type: "message";
  role: "user" | "assistant";
  text: string;
  // Codex 원본에 아직/끝내 기록되지 않은 사용자 입력(플랫폼이 보존). 모델이 본 이력이 아니다.
  source?: "platform";
  status?: "pending" | "not_recorded";
  // 고정 모델 확인 실패(응답 모델 불일치·우회)로 중단된 실행의 항목. 정상 응답이 아니다.
  modelMismatch?: boolean;
  // 사용자 메시지가 접수된 범위(서버 기록). 이전 기록에는 없을 수 있다.
  scope?: AcceptedScope;
}

export interface ToolCallItem {
  id: string;
  type: "tool_call";
  name: string;
  input: unknown;
  output: unknown;
  // rejected: 이 질문의 범위(#14부터 공개 글 읽기 등)에서 허용되지 않아 서버가 거부한 호출(error.code tool_not_allowed)
  status: string;
  error?: { code: string };
  modelMismatch?: boolean;
}

export type SessionItem = MessageItem | ToolCallItem;

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

// 관리자 화면: 계정 승인·역할
export interface AdminAccount {
  accountId: string;
  githubId: string;
  login: string;
  role: "admin" | "editor" | "commenter";
  isApproved: boolean;
  createdAt: string;
  updatedAt: string;
  // 동시 변경 감지 값(updatedAt의 정확한 문자열). 그대로 expectedVersion으로 보낸다.
  version: string;
}
