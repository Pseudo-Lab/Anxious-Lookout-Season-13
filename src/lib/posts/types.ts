// #28 글(post) 계약 타입(api-contracts/issue28-posts.md). 문서(research items)와 별도 모델이다.

export interface PublicPostDocument {
  id: string;
  title: string;
  // 공개 문서가 있을 때만 `{BASE_PATH}/public/document/?id=…`. null이면 링크를 만들지 않는다.
  publicUrl: string | null;
}

/** 현재 발행본. 목록 응답에는 content가 없다. */
export interface PublicPost {
  id: string;
  postId: string;
  versionId: string;
  slug: string;
  title: string;
  description: string;
  content?: string;
  tags: string[];
  coverImageUrl: string | null;
  // 이관된 MDX 글은 login이 null이고 원 작성자 이름만 있다
  author: { login: string | null; name: string };
  publishedAt: string;
  documents: PublicPostDocument[];
}

export interface PostTag {
  tag: string;
  count: number;
}

/** 글 한 버전의 내용·메타·문서 참조. 초안·버전·제안이 같은 모양이다. */
export interface PostText {
  title: string;
  content: string;
  description: string;
  slug: string;
  tags: string[];
  coverImageId: string | null;
  documentIds: string[];
}

export interface PostVersion extends Omit<PostText, "content"> {
  id: string;
  postId: string;
  number: number;
  // 목록 응답에는 없다
  content?: string;
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

export interface PostDraft extends Omit<PostText, "content"> {
  id: string;
  // 아직 만들지 않은 새 글 초안이면 null
  postId: string | null;
  revision: number;
  basePostVersion: number | null;
  baseVersionId: string | null;
  // 목록 응답에는 없다
  content?: string;
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
