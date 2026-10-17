import { apiUrl, fetchJson } from "@/lib/api/client";
import { BASE_PATH } from "@/lib/constants";
import { listQuery, mutate, parsePage, type MutationContext } from "@/lib/research/api";
import type {
  Post,
  PostDocumentOption,
  PostDraft,
  PostImage,
  PostProposal,
  PostPublicationPreview,
  PostTag,
  PostText,
  PostVersion,
  PublicPost,
  PublicPostDocument,
} from "@/lib/posts/types";

// api-contracts/issue28-posts.md 클라이언트. 응답 형식이 계약과 다르면 invalid로 처리하고 추정값으로 채우지 않는다.

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isId = (v: unknown): v is string => typeof v === "string" && v !== "";
const isInt = (v: unknown): v is number => Number.isInteger(v);
const isStrList = (v: unknown): v is string[] => Array.isArray(v) && v.every(isStr);
const enc = encodeURIComponent;

function parseDocRef(v: unknown): PublicPostDocument | null {
  if (!isObj(v) || !isId(v.id) || !isStr(v.title) || (v.publicUrl !== null && !isStr(v.publicUrl))) return null;
  return { id: v.id, title: v.title, publicUrl: v.publicUrl };
}

function parsePublicPost(v: unknown): PublicPost | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    !isId(v.postId) ||
    !isId(v.versionId) ||
    !isStr(v.slug) ||
    !isStr(v.title) ||
    !isStr(v.description) ||
    !isStrList(v.tags) ||
    (v.coverImageUrl !== null && !isStr(v.coverImageUrl)) ||
    !isObj(v.author) ||
    (v.author.login !== null && !isStr(v.author.login)) ||
    !isStr(v.author.name) ||
    !isStr(v.publishedAt) ||
    !Array.isArray(v.documents) ||
    (v.content !== undefined && !isStr(v.content))
  ) {
    return null;
  }
  const documents = v.documents.map(parseDocRef);
  if (documents.some((d) => d === null)) return null;
  const post: PublicPost = {
    id: v.id,
    postId: v.postId,
    versionId: v.versionId,
    slug: v.slug,
    title: v.title,
    description: v.description,
    tags: v.tags,
    coverImageUrl: v.coverImageUrl,
    author: { login: v.author.login, name: v.author.name },
    publishedAt: v.publishedAt,
    documents: documents as PublicPostDocument[],
  };
  if (isStr(v.content)) post.content = v.content;
  return post;
}

const parsePublicPostDetail = (v: unknown) => {
  const post = parsePublicPost(v);
  return post && post.content !== undefined ? post : null;
};

function parseTag(v: unknown): PostTag | null {
  if (!isObj(v) || !isStr(v.tag) || !isInt(v.count)) return null;
  return { tag: v.tag, count: v.count };
}

/** PostText에서 content를 뺀 메타(목록 응답에는 content가 없다). */
function parseMeta(v: Obj): Omit<PostText, "content"> | null {
  if (
    !isStr(v.title) ||
    !isStr(v.description) ||
    !isStr(v.slug) ||
    !isStrList(v.tags) ||
    (v.coverImageId !== null && !isStr(v.coverImageId)) ||
    !isStrList(v.documentIds) ||
    (v.content !== undefined && !isStr(v.content))
  ) {
    return null;
  }
  return {
    title: v.title,
    description: v.description,
    slug: v.slug,
    tags: v.tags,
    coverImageId: v.coverImageId,
    documentIds: v.documentIds,
  };
}

function parseVersion(v: unknown): PostVersion | null {
  if (!isObj(v) || !isId(v.id) || !isId(v.postId) || !isInt(v.number) || !isStr(v.createdAt)) return null;
  const meta = parseMeta(v);
  if (!meta) return null;
  const out: PostVersion = { id: v.id, postId: v.postId, number: v.number, createdAt: v.createdAt, ...meta };
  if (isStr(v.content)) out.content = v.content;
  return out;
}

const parseFullVersion = (v: unknown) => {
  const version = parseVersion(v);
  return version && version.content !== undefined ? version : null;
};

function parsePost(v: unknown): Post | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    !isInt(v.version) ||
    typeof v.archived !== "boolean" ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt) ||
    !isObj(v.author) ||
    !isStr(v.author.login)
  ) {
    return null;
  }
  const latestVersion = parseVersion(v.latestVersion);
  if (!latestVersion) return null;
  let publication: Post["publication"] = null;
  if (v.publication !== null) {
    const p = v.publication;
    if (!isObj(p) || !isId(p.id) || !isId(p.versionId) || !isStr(p.publishedAt) || !isStr(p.slug)) return null;
    publication = { id: p.id, versionId: p.versionId, publishedAt: p.publishedAt, slug: p.slug };
  }
  return {
    id: v.id,
    version: v.version,
    archived: v.archived,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
    author: { login: v.author.login },
    latestVersion,
    publication,
  };
}

const parseFullPost = (v: unknown) => {
  const post = parsePost(v);
  return post && post.latestVersion.content !== undefined ? post : null;
};

function parseDraft(v: unknown): PostDraft | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    (v.postId !== null && !isId(v.postId)) ||
    !isInt(v.revision) ||
    (v.basePostVersion !== null && !isInt(v.basePostVersion)) ||
    (v.baseVersionId !== null && !isId(v.baseVersionId)) ||
    (v.state !== "active" && v.state !== "committed" && v.state !== "discarded") ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt)
  ) {
    return null;
  }
  const meta = parseMeta(v);
  if (!meta) return null;
  const out: PostDraft = {
    id: v.id,
    postId: v.postId,
    revision: v.revision,
    basePostVersion: v.basePostVersion,
    baseVersionId: v.baseVersionId,
    state: v.state,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
    ...meta,
  };
  if (isStr(v.content)) out.content = v.content;
  return out;
}

const parseFullDraft = (v: unknown) => {
  const draft = parseDraft(v);
  return draft && draft.content !== undefined ? draft : null;
};

function parsePostDraftRef(v: unknown): { draft: PostDraft | null } | null {
  if (!isObj(v)) return null;
  if (v.draft === null) return { draft: null };
  const draft = parseFullDraft(v.draft);
  return draft ? { draft } : null;
}

function parseCommit(v: unknown): { draft: PostDraft; post: Post } | null {
  if (!isObj(v)) return null;
  const draft = parseDraft(v.draft);
  const post = parseFullPost(v.post);
  return draft && post ? { draft, post } : null;
}

function parseOption(v: unknown): PostDocumentOption | null {
  if (!isObj(v) || !isId(v.id) || !isStr(v.title) || !isInt(v.version) || !isId(v.latestVersionId)) return null;
  return { id: v.id, title: v.title, version: v.version, latestVersionId: v.latestVersionId };
}

function parsePreview(v: unknown): PostPublicationPreview | null {
  if (
    !isObj(v) ||
    !isId(v.postId) ||
    !isId(v.versionId) ||
    !isInt(v.expectedVersion) ||
    !isStr(v.previewToken) ||
    typeof v.publishable !== "boolean" ||
    !isStr(v.content) ||
    !isObj(v.author) ||
    !isStr(v.author.login) ||
    !Array.isArray(v.documents) ||
    !Array.isArray(v.images)
  ) {
    return null;
  }
  const meta = parseMeta(v);
  const documents = v.documents.map(parseDocRef);
  const images = v.images.map((i) => (isObj(i) && isId(i.id) && isStr(i.url) ? { id: i.id, url: i.url } : null));
  if (!meta || documents.some((d) => d === null) || images.some((i) => i === null)) return null;
  let currentPublication: PublicPost | null = null;
  if (v.currentPublication !== null) {
    currentPublication = parsePublicPostDetail(v.currentPublication);
    if (!currentPublication) return null;
  }
  return {
    postId: v.postId,
    versionId: v.versionId,
    expectedVersion: v.expectedVersion,
    previewToken: v.previewToken,
    publishable: v.publishable,
    ...meta,
    content: v.content,
    author: { login: v.author.login },
    documents: documents as PublicPostDocument[],
    images: images as { id: string; url: string }[],
    currentPublication,
  };
}

function parseProposal(v: unknown): PostProposal | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    (v.postId !== null && !isId(v.postId)) ||
    !isInt(v.version) ||
    (v.basePostVersion !== null && !isInt(v.basePostVersion)) ||
    (v.baseVersionId !== null && !isId(v.baseVersionId)) ||
    (v.baseDraftId !== null && !isId(v.baseDraftId)) ||
    (v.baseDraftRevision !== null && !isInt(v.baseDraftRevision)) ||
    (v.status !== "pending" && v.status !== "accepted" && v.status !== "discarded") ||
    (v.acceptedPostId !== null && !isId(v.acceptedPostId)) ||
    (v.acceptedVersionId !== null && !isId(v.acceptedVersionId)) ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt)
  ) {
    return null;
  }
  const meta = parseMeta(v);
  if (!meta) return null;
  const out: PostProposal = {
    id: v.id,
    postId: v.postId,
    version: v.version,
    basePostVersion: v.basePostVersion,
    baseVersionId: v.baseVersionId,
    baseDraftId: v.baseDraftId,
    baseDraftRevision: v.baseDraftRevision,
    status: v.status,
    acceptedPostId: v.acceptedPostId,
    acceptedVersionId: v.acceptedVersionId,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
    ...meta,
  };
  if (isStr(v.content)) out.content = v.content;
  return out;
}

const parseFullProposal = (v: unknown) => {
  const p = parseProposal(v);
  return p && p.content !== undefined ? p : null;
};

function parseAccept(v: unknown): { proposal: PostProposal; post: Post; draft: PostDraft | null } | null {
  if (!isObj(v)) return null;
  const proposal = parseProposal(v.proposal);
  const post = parseFullPost(v.post);
  const draft = v.draft === null ? null : parseDraft(v.draft);
  return proposal && post && (v.draft === null || draft) ? { proposal, post, draft } : null;
}

function parseImage(v: unknown): PostImage | null {
  if (!isObj(v) || !isId(v.id) || !isStr(v.url) || !isStr(v.mimeType) || !isInt(v.size) || !isInt(v.width) || !isInt(v.height) || !isStr(v.createdAt)) return null;
  return { id: v.id, url: v.url, mimeType: v.mimeType, size: v.size, width: v.width, height: v.height, createdAt: v.createdAt };
}

function parseUnpublish(v: unknown): { id: string; version: number } | null {
  if (!isObj(v) || !isId(v.id) || !isInt(v.version) || v.publication !== null) return null;
  return { id: v.id, version: v.version };
}

// ---- 공개 (비로그인 조회) ----

export const listPublicPosts = (cursor?: string | null, opts: { tag?: string; limit?: number } = {}) =>
  fetchJson(
    apiUrl(
      `/public/posts${listQuery(cursor, {
        ...(opts.tag !== undefined ? { tag: opts.tag } : {}),
        ...(opts.limit !== undefined ? { limit: String(opts.limit) } : {}),
      })}`
    ),
    parsePage(parsePublicPost)
  );
export const getPublicPost = (slug: string) => fetchJson(apiUrl(`/public/posts/${enc(slug)}`), parsePublicPostDetail);
export const listPostTags = (cursor?: string | null) =>
  fetchJson(apiUrl(`/public/post-tags${listQuery(cursor, { limit: "100" })}`), parsePage(parseTag));

// ---- 내 글 ----

export const listPosts = (cursor?: string | null, q?: string) =>
  fetchJson(apiUrl(`/research/posts${listQuery(cursor, q ? { q } : undefined)}`), parsePage(parsePost));
export const getPost = (id: string) => fetchJson(apiUrl(`/research/posts/${enc(id)}`), parseFullPost);
export const listPostVersions = (id: string, cursor?: string | null) =>
  fetchJson(apiUrl(`/research/posts/${enc(id)}/versions${listQuery(cursor)}`), parsePage(parseVersion));
export const getPostVersion = (id: string, versionId: string) =>
  fetchJson(apiUrl(`/research/posts/${enc(id)}/versions/${enc(versionId)}`), parseFullVersion);
export const archivePost = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/posts/${enc(id)}`, "DELETE", { expectedVersion }, parsePost, ctx);
export const listPostDocumentOptions = (q: string, cursor?: string | null) =>
  fetchJson(apiUrl(`/research/post-document-options${listQuery(cursor, q ? { q } : undefined)}`), parsePage(parseOption));

// ---- 글 서버 초안 ----

/** 새 초안. 기존 글이면 base(글 version·현재 snapshot 번호·ID)를 함께 보낸다. */
export const createPostDraft = (
  input: PostText & { postId?: string; basePostVersion?: number; baseVersionId?: string },
  ctx: MutationContext
) => mutate("/research/post-drafts", "POST", input, parseFullDraft, ctx);
export const listPostDrafts = (cursor?: string | null) =>
  fetchJson(apiUrl(`/research/post-drafts${listQuery(cursor)}`), parsePage(parseDraft));
export const getPostDraft = (id: string) => fetchJson(apiUrl(`/research/post-drafts/${enc(id)}`), parseFullDraft);
export const getPostDraftOf = (postId: string) =>
  fetchJson(apiUrl(`/research/posts/${enc(postId)}/draft`), parsePostDraftRef);
export const savePostDraft = (id: string, input: PostText, expectedDraftRevision: number, ctx: MutationContext) =>
  mutate(`/research/post-drafts/${enc(id)}`, "PUT", { ...input, expectedDraftRevision }, parseFullDraft, ctx);
export const discardPostDraft = (id: string, expectedDraftRevision: number, ctx: MutationContext) =>
  mutate(`/research/post-drafts/${enc(id)}`, "DELETE", { expectedDraftRevision }, parseDraft, ctx);
/** 명시 저장: 버전 1개. 기존 글은 expectedVersion 필수, base가 바뀌었으면 비교 뒤 현재 snapshot ID를 resolvedBaseVersionId로. */
export const commitPostDraft = (
  id: string,
  body: { expectedDraftRevision: number; expectedVersion?: number; resolvedBaseVersionId?: string },
  ctx: MutationContext
) => mutate(`/research/post-drafts/${enc(id)}/commit`, "POST", body, parseCommit, ctx);

// ---- 발행 ----

export const getPostPublicationPreview = (id: string, versionId: string) =>
  fetchJson(apiUrl(`/research/posts/${enc(id)}/publication-preview?versionId=${enc(versionId)}`), parsePreview);
/** 미리보기에 보인 버전·문서·이미지 상태로 발행한다. 그사이 바뀌었으면 409 preview_changed. */
export const publishPost = (preview: PostPublicationPreview, ctx: MutationContext) =>
  mutate(
    `/research/posts/${enc(preview.postId)}/publications`,
    "POST",
    { expectedVersion: preview.expectedVersion, versionId: preview.versionId, previewToken: preview.previewToken },
    parsePublicPostDetail,
    ctx
  );
export const unpublishPost = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/posts/${enc(id)}/publication`, "DELETE", { expectedVersion }, parseUnpublish, ctx);

// ---- Codex 글 제안 ----

/** postId를 주면 그 글의 제안, 생략하면 새 글 제안을 포함한 전체(모든 상태). */
export const listPostProposals = (cursor?: string | null, postId?: string) =>
  fetchJson(apiUrl(`/research/post-proposals${listQuery(cursor, postId ? { postId } : undefined)}`), parsePage(parseProposal));
export const getPostProposal = (id: string) => fetchJson(apiUrl(`/research/post-proposals/${enc(id)}`), parseFullProposal);
/** 사람이 비교한 최종 전체 내용으로 수락한다. 기존 글은 현재 version·snapshot, 초안이 걸려 있으면 그 초안의 현재 revision을 함께 보낸다. */
export const acceptPostProposal = (
  id: string,
  body: PostText & {
    expectedProposalVersion: number;
    expectedVersion?: number;
    resolvedBaseVersionId?: string;
    draftId?: string;
    expectedDraftRevision?: number;
  },
  ctx: MutationContext
) => mutate(`/research/post-proposals/${enc(id)}/accept`, "POST", body, parseAccept, ctx);
export const discardPostProposal = (id: string, expectedProposalVersion: number, ctx: MutationContext) =>
  mutate(`/research/post-proposals/${enc(id)}/discard`, "POST", { expectedProposalVersion }, parseProposal, ctx);

// ---- 이미지(D2) ----

/** 자체 이미지의 정규 주소(계약 Q3). 서버 응답의 url과 같다. */
export const ownImageUrl = (id: string) => `${BASE_PATH}/api/media/post-images/${id.toLowerCase()}`;

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const IMAGE_MAX_BYTES = 5 * 1024 * 1024;
const UPLOAD_TIMEOUT_MS = 60_000;

/** 원본 바이트를 그대로 보낸다(multipart 아님). Content-Type은 파일의 실제 형식이며 서버가 다시 검사·재인코딩한다. */
export const uploadPostImage = (file: Blob, ctx: MutationContext) =>
  fetchJson(
    apiUrl("/research/post-images"),
    parseImage,
    {
      method: "POST",
      headers: { "Content-Type": file.type, "X-CSRF-Token": ctx.csrfToken, "Idempotency-Key": ctx.idempotencyKey },
      body: file,
    },
    UPLOAD_TIMEOUT_MS
  );
export const listPostImages = (cursor?: string | null) =>
  fetchJson(apiUrl(`/research/post-images${listQuery(cursor)}`), parsePage(parseImage));
export const deletePostImage = (id: string, ctx: MutationContext) =>
  mutate(`/research/post-images/${enc(id)}`, "DELETE", {}, (v) => (isObj(v) && v.deleted === true ? { id } : null), ctx);
