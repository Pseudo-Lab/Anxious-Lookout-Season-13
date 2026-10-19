// #28 글(post) 계약 타입(api-contracts/issue28-posts.md, #31 개정 issue31-write-ux.md). 문서(research items)와 별도 모델이다.
// #32: 본문(content)은 post-blocks v1 문서다(api-contracts/issue32-blocks-draft.md §11). textProjection은 서버가 계산한 읽기 투영.

import type { BlockDocument } from "@/lib/blocks/types";
import type { FormatChange } from "@/lib/blocks/projection";

export interface PublicPostDocument {
  id: string;
  title: string;
  // 공개 문서가 있을 때만 `{BASE_PATH}/public/document/?id=…`. null이면 링크를 만들지 않는다.
  publicUrl: string | null;
}

/** 발행본에 공개되는 자료(#31 W3): 제목과 원본 주소만. 자료 본문·메모는 공개하지 않는다. */
export interface PublicPostMaterial {
  id: string;
  title: string;
  sourceUrl: string;
}

/** 현재 발행본. 목록 응답에는 content가 없다. */
export interface PublicPost {
  id: string;
  postId: string;
  versionId: string;
  slug: string;
  title: string;
  description: string;
  // 공개 본문(post-blocks). renderer가 그리기 전에 다시 검사하므로 모르는 형식도 여기서는 그대로 둔다.
  content?: unknown;
  textProjection?: string;
  tags: string[];
  coverImageUrl: string | null;
  // 이관된 MDX 글은 login이 null이고 원 작성자 이름만 있다
  author: { login: string | null; name: string };
  publishedAt: string;
  documents: PublicPostDocument[];
  materials: PublicPostMaterial[];
}

export interface PostTag {
  tag: string;
  count: number;
}

/**
 * 사람·Codex가 쓰는 글 내용·메타·참조(초안 저장·제안 수락 입력). 주소(slug)는 넣지 않는다(#31 W1: 서버가 첫 저장 때 정한다).
 * materialIds는 언제나 최종 선택 집합을 명시해 보낸다(생략하면 서버가 문서→자료를 한 번 펼친다).
 */
export interface PostText {
  title: string;
  content: BlockDocument;
  description: string;
  tags: string[];
  coverImageId: string | null;
  documentIds: string[];
  materialIds: string[];
}

/** 선택 자료가 어느 연결 문서에서 왔는지(출력 전용). documentIds가 비면 직접 추가한 자료다. */
export interface PostMaterialSource {
  materialId: string;
  documentIds: string[];
}

/** 응답의 글 메타. 새 글 초안·제안의 slug는 ""이다. */
export interface PostMeta extends Omit<PostText, "content"> {
  slug: string;
  materialSources: PostMaterialSource[];
}

export interface PostVersion extends PostMeta {
  id: string;
  postId: string;
  number: number;
  // 목록 응답에는 없다
  content?: BlockDocument;
  textProjection?: string;
  createdAt: string;
}

export interface PostPublicationRef {
  id: string;
  versionId: string;
  publishedAt: string;
  slug: string;
}

export interface Post {
  id: string;
  version: number;
  archived: boolean;
  createdAt: string;
  updatedAt: string;
  author: { login: string };
  latestVersion: PostVersion;
  publication: PostPublicationRef | null;
}

export type PostDraftState = "active" | "committed" | "discarded";

export interface PostDraft extends PostMeta {
  id: string;
  // 아직 만들지 않은 새 글 초안이면 null
  postId: string | null;
  revision: number;
  basePostVersion: number | null;
  baseVersionId: string | null;
  // 목록 응답에는 없다
  content?: BlockDocument;
  textProjection?: string;
  state: PostDraftState;
  createdAt: string;
  updatedAt: string;
}

export interface PostDocumentOption {
  id: string;
  title: string;
  version: number;
  latestVersionId: string;
}

export interface PostMaterialOption extends PostDocumentOption {
  sourceUrl: string;
}

/** 연결 문서에서 찾은 자료(문서→자료 직접 관계). documentIds는 그 자료를 가리키는 요청 문서들. */
export interface ResolvedPostMaterial extends PostMaterialOption {
  documentIds: string[];
}

/** 발행 전 미리보기(선택 버전 기준). previewToken은 이 상태(글·문서·이미지)에 묶여 발행 때 다시 검증된다. */
export interface PostPublicationPreview extends PostMeta {
  content: unknown;
  textProjection: string;
  postId: string;
  versionId: string;
  expectedVersion: number;
  previewToken: string;
  publishable: boolean;
  author: { login: string };
  // 비공개 문서도 제목은 글에 공개된다(본문은 아님)
  documents: PublicPostDocument[];
  // 공개될 자료 제목·원본 주소
  materials: PublicPostMaterial[];
  images: { id: string; url: string }[];
  currentPublication: PublicPost | null;
}

export type PostProposalStatus = "pending" | "accepted" | "discarded";

/** Codex 글 제안(#28 D1). 수락하기 전에는 글·초안을 바꾸지 않는다. 새 글 제안은 postId가 null이다. */
export interface PostProposal extends PostMeta {
  id: string;
  postId: string | null;
  version: number;
  basePostVersion: number | null;
  baseVersionId: string | null;
  // 사람이 작성 중이던 초안에 대한 제안이면 그 초안(새 글 제안은 수락 때 이 초안이 필요하다)
  baseDraftId: string | null;
  baseDraftRevision: number | null;
  // 목록 응답에는 없다
  content?: BlockDocument;
  textProjection?: string;
  // 제안 생성 때 서버가 계산한 기준 대비 비교(수락 화면은 comparison API로 다시 계산한다)
  baseTextProjection?: string;
  formatChanges?: FormatChange[];
  // Markdown → 블록 변환 안내(예: code_language_downgraded)
  conversionWarnings?: { code: string }[];
  status: PostProposalStatus;
  acceptedPostId: string | null;
  acceptedVersionId: string | null;
  createdAt: string;
  updatedAt: string;
}

/** 직접 올린 이미지(#28 D2). url은 `{BASE_PATH}/api/media/post-images/{id}` 그대로 본문·표지에 쓴다. */
export interface PostImage {
  id: string;
  url: string;
  mimeType: string;
  size: number;
  width: number;
  height: number;
  createdAt: string;
}

/** POST /post-proposals/{id}/comparison: 지금 기준(현재 글 head 또는 새 글 연결 초안) 대비 후보 비교. 저장하지 않는다. */
export interface PostProposalComparison {
  baseVersionId: string | null;
  baseDraftRevision: number | null;
  baseContentHash: string;
  candidateHash: string;
  baseTextProjection: string;
  textProjection: string;
  formatChanges: FormatChange[];
}
