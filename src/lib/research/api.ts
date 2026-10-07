import { apiUrl, fetchJson, type FetchFailure, type FetchResult } from "@/lib/api/client";
import {
  CONTENT_KINDS,
  type ArchiveResult,
  type CodexStatus,
  type ContentKind,
  type DocumentSummary,
  type Endpoint,
  type ItemType,
  type Material,
  type MaterialSummary,
  type MessageItem,
  type Page,
  type PublicDocumentSummary,
  type PublicSnapshot,
  type Publication,
  type PublishedMaterial,
  type RawSessionItem,
  type Relation,
  type ResearchDocument,
  type SessionDetail,
  type SessionItem,
  type SessionSummary,
  type Version,
  type VersionRef,
  type VersionSummary,
} from "@/lib/research/types";

// api-contracts/m3-research-data.md 클라이언트. 응답 형식이 계약과 다르면 invalid로 처리하고
// 추정값으로 채우지 않는다.

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isId = (v: unknown): v is string => typeof v === "string" && v !== "";
const isInt = (v: unknown): v is number => Number.isInteger(v);
const isKind = (v: unknown): v is ContentKind => CONTENT_KINDS.includes(v as ContentKind);
const isItemType = (v: unknown): v is ItemType => v === "material" || v === "document";

function parseVersionRef(v: unknown): VersionRef | null {
  if (!isObj(v) || !isId(v.id) || !isInt(v.number) || !isStr(v.createdAt)) return null;
  return { id: v.id, number: v.number, createdAt: v.createdAt };
}

function parseItemBase(v: Obj) {
  const latestVersion = parseVersionRef(v.latestVersion);
  if (
    !isId(v.id) ||
    !isStr(v.title) ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt) ||
    !isInt(v.version) ||
    !latestVersion ||
    typeof v.archived !== "boolean"
  ) {
    return null;
  }
  return {
    id: v.id,
    title: v.title,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
    version: v.version,
    latestVersion,
    archived: v.archived,
  };
}

function parseMaterialSummary(v: unknown): MaterialSummary | null {
  if (!isObj(v) || v.type !== "material") return null;
  const base = parseItemBase(v);
  if (!base || !isStr(v.sourceUrl) || !isStr(v.collectedAt) || !isKind(v.contentKind)) return null;
  return { ...base, type: "material", sourceUrl: v.sourceUrl, collectedAt: v.collectedAt, contentKind: v.contentKind };
}

function parseMaterial(v: unknown): Material | null {
  const summary = parseMaterialSummary(v);
  if (!summary || !isObj(v) || !isStr(v.content)) return null;
  return { ...summary, content: v.content };
}

function parsePublication(v: unknown): Publication | null | undefined {
  if (v === null) return null;
  if (
    !isObj(v) ||
    !isId(v.id) ||
    v.state !== "published" ||
    !isId(v.versionId) ||
    !isStr(v.publishedAt) ||
    !Array.isArray(v.materialIds) ||
    !v.materialIds.every(isId) ||
    !Array.isArray(v.materials) ||
    !v.materials.every((m) => isObj(m) && isId(m.id) && isId(m.versionId))
  ) {
    return undefined;
  }
  return {
    id: v.id,
    state: "published",
    versionId: v.versionId,
    publishedAt: v.publishedAt,
    materialIds: v.materialIds,
    materials: (v.materials as Obj[]).map((m) => ({ id: m.id as string, versionId: m.versionId as string })),
  };
}

function parseDocumentSummary(v: unknown): DocumentSummary | null {
  if (!isObj(v) || v.type !== "document") return null;
  const base = parseItemBase(v);
  const publication = parsePublication(v.publication);
  if (!base || publication === undefined) return null;
  return { ...base, type: "document", publication };
}

function parseDocument(v: unknown): ResearchDocument | null {
  const summary = parseDocumentSummary(v);
  if (!summary || !isObj(v) || !isStr(v.content)) return null;
  return { ...summary, content: v.content };
}

function parseVersionSummary(v: unknown): VersionSummary | null {
  if (!isObj(v) || !isId(v.id) || !isId(v.itemId) || !isInt(v.number) || !isStr(v.title) || !isStr(v.createdAt)) {
    return null;
  }
  const out: VersionSummary = { id: v.id, itemId: v.itemId, number: v.number, title: v.title, createdAt: v.createdAt };
  if (isStr(v.sourceUrl)) out.sourceUrl = v.sourceUrl;
  if (isStr(v.collectedAt)) out.collectedAt = v.collectedAt;
  if (isKind(v.contentKind)) out.contentKind = v.contentKind;
  return out;
}

function parseVersion(v: unknown): Version | null {
  const summary = parseVersionSummary(v);
  if (!summary || !isObj(v) || !isStr(v.content)) return null;
  return { ...summary, content: v.content };
}

function parseEndpoint(v: unknown): Endpoint | null {
  if (!isObj(v) || !isItemType(v.type) || !isId(v.id) || !isStr(v.title)) return null;
  return { type: v.type, id: v.id, title: v.title };
}

function parseRelation(v: unknown): Relation | null {
  if (!isObj(v)) return null;
  const source = parseEndpoint(v.source);
  const target = parseEndpoint(v.target);
  if (
    !source ||
    !target ||
    !isId(v.id) ||
    !isStr(v.kind) ||
    !isStr(v.description) ||
    typeof v.directed !== "boolean" ||
    !isInt(v.version) ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt)
  ) {
    return null;
  }
  const out: Relation = {
    id: v.id,
    source,
    target,
    kind: v.kind,
    description: v.description,
    directed: v.directed,
    version: v.version,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
  };
  if (v.direction === "outgoing" || v.direction === "incoming" || v.direction === "bidirectional") {
    out.direction = v.direction;
  }
  return out;
}

function parsePage<T>(parse: (v: unknown) => T | null) {
  return (body: unknown): Page<T> | null => {
    if (!isObj(body) || !Array.isArray(body.items)) return null;
    if (body.nextCursor !== null && !isStr(body.nextCursor)) return null;
    const items: T[] = [];
    for (const raw of body.items) {
      const item = parse(raw);
      if (item === null) return null;
      items.push(item);
    }
    return { items, nextCursor: body.nextCursor };
  };
}

function parseArchive(v: unknown): ArchiveResult | null {
  if (!isObj(v) || !isId(v.id) || v.archived !== true || !isInt(v.version)) return null;
  return { id: v.id, archived: true, version: v.version };
}

function parseAuthor(v: unknown): { login: string } | null {
  return isObj(v) && isStr(v.login) ? { login: v.login } : null;
}

function parsePublishedMaterial(v: unknown): PublishedMaterial | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    !isId(v.versionId) ||
    !isStr(v.title) ||
    !isStr(v.sourceUrl) ||
    !isStr(v.collectedAt) ||
    !isKind(v.contentKind)
  ) {
    return null;
  }
  // 노출 범위 정책(전체/메타데이터만/발췌)이 미정이라 content가 없을 수 있다.
  return {
    id: v.id,
    versionId: v.versionId,
    title: v.title,
    sourceUrl: v.sourceUrl,
    collectedAt: v.collectedAt,
    contentKind: v.contentKind,
    content: isStr(v.content) ? v.content : null,
  };
}

function parsePublicSnapshot(v: unknown): PublicSnapshot | null {
  if (!isObj(v) || !Array.isArray(v.materials)) return null;
  const author = parseAuthor(v.author);
  const materials = v.materials.map(parsePublishedMaterial);
  if (
    !author ||
    materials.some((m) => m === null) ||
    !isId(v.id) ||
    !isId(v.documentId) ||
    !isId(v.versionId) ||
    !isStr(v.title) ||
    !isStr(v.content) ||
    !isStr(v.publishedAt)
  ) {
    return null;
  }
  return {
    id: v.id,
    documentId: v.documentId,
    versionId: v.versionId,
    title: v.title,
    content: v.content,
    publishedAt: v.publishedAt,
    materials: materials as PublishedMaterial[],
    author,
  };
}

function parsePublicSummary(v: unknown): PublicDocumentSummary | null {
  if (!isObj(v)) return null;
  const author = parseAuthor(v.author);
  if (!author || !isId(v.id) || !isId(v.documentId) || !isStr(v.title) || !isStr(v.publishedAt)) return null;
  return { id: v.id, documentId: v.documentId, title: v.title, publishedAt: v.publishedAt, author };
}

function parseCodexStatus(v: unknown): CodexStatus | null {
  if (!isObj(v) || typeof v.available !== "boolean") return null;
  if (v.reason !== null && v.reason !== "not_configured" && v.reason !== "unavailable") return null;
  if (v.verification !== "unverified" && v.verification !== "fixture" && v.verification !== "real") return null;
  return { available: v.available, reason: v.reason, verification: v.verification };
}

function parseSessionSummary(v: unknown): SessionSummary | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    !isStr(v.title) ||
    (v.state !== "idle" && v.state !== "running" && v.state !== "failed") ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt) ||
    !isInt(v.version)
  ) {
    return null;
  }
  return { id: v.id, title: v.title, state: v.state, createdAt: v.createdAt, updatedAt: v.updatedAt, version: v.version };
}

function parseSessionItem(v: unknown): SessionItem | null {
  if (!isObj(v) || !isId(v.id)) return null;
  if (v.type === "message" && (v.role === "user" || v.role === "assistant") && isStr(v.text)) {
    const item: MessageItem = { id: v.id, type: "message", role: v.role, text: v.text };
    if (v.source !== undefined) {
      if (v.source !== "platform" || (v.status !== "pending" && v.status !== "not_recorded")) return null;
      item.source = "platform";
      item.status = v.status;
    }
    return item;
  }
  if (v.type === "tool_call" && isStr(v.name) && isStr(v.status)) {
    return { id: v.id, type: "tool_call", name: v.name, input: v.input, output: v.output, status: v.status };
  }
  return null;
}

function parseSessionDetail(v: unknown): SessionDetail | null {
  const summary = parseSessionSummary(v);
  if (!summary || !isObj(v) || !Array.isArray(v.items)) return null;
  const items = v.items.map(parseSessionItem);
  if (items.some((i) => i === null)) return null;
  let error: SessionDetail["error"] = null;
  if (v.error !== null) {
    if (!isObj(v.error) || !isStr(v.error.code) || !isStr(v.error.message)) return null;
    error = { code: v.error.code, message: v.error.message };
  }
  return { ...summary, items: items as SessionItem[], error };
}

function parseRawItem(v: unknown): RawSessionItem | null {
  if (!isObj(v) || !isId(v.id) || v.format !== "json" || !("raw" in v)) return null;
  return { id: v.id, format: "json", raw: v.raw };
}

// ---- 요청 공통 ----

export interface MutationContext {
  csrfToken: string;
  idempotencyKey: string;
}

function mutate<T>(
  path: `/${string}`,
  method: "POST" | "PATCH" | "DELETE",
  body: unknown,
  parse: (v: unknown) => T | null,
  ctx: MutationContext
): Promise<FetchResult<T>> {
  return fetchJson(apiUrl(path), parse, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": ctx.csrfToken,
      "Idempotency-Key": ctx.idempotencyKey,
    },
    body: JSON.stringify(body),
  });
}

function listQuery(cursor?: string | null, extra?: Record<string, string>): string {
  const params = new URLSearchParams({ limit: "30", ...extra });
  if (cursor) params.set("cursor", cursor);
  return `?${params.toString()}`;
}

const enc = encodeURIComponent;
const collection = (type: ItemType) => (type === "material" ? "materials" : "documents");

// ---- 자료·문서 ----

export interface MaterialInput {
  title: string;
  sourceUrl: string;
  collectedAt: string;
  contentKind: ContentKind;
  content: string;
}

export interface DocumentInput {
  title: string;
  content: string;
}

export const listMaterials = (cursor?: string | null, archived = false) =>
  fetchJson(
    apiUrl(`/research/materials${listQuery(cursor, archived ? { archived: "true" } : undefined)}`),
    parsePage(parseMaterialSummary)
  );

export const listDocuments = (cursor?: string | null, archived = false) =>
  fetchJson(
    apiUrl(`/research/documents${listQuery(cursor, archived ? { archived: "true" } : undefined)}`),
    parsePage(parseDocumentSummary)
  );

export const getMaterial = (id: string) => fetchJson(apiUrl(`/research/materials/${enc(id)}`), parseMaterial);
export const getDocument = (id: string) => fetchJson(apiUrl(`/research/documents/${enc(id)}`), parseDocument);

export const createMaterial = (input: MaterialInput, ctx: MutationContext) =>
  mutate("/research/materials", "POST", input, parseMaterial, ctx);
export const createDocument = (input: DocumentInput, ctx: MutationContext) =>
  mutate("/research/documents", "POST", input, parseDocument, ctx);

export const updateMaterial = (id: string, input: MaterialInput, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/materials/${enc(id)}`, "PATCH", { ...input, expectedVersion }, parseMaterial, ctx);
export const updateDocument = (id: string, input: DocumentInput, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/documents/${enc(id)}`, "PATCH", { ...input, expectedVersion }, parseDocument, ctx);

export const archiveItem = (type: ItemType, id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/${collection(type)}/${enc(id)}`, "DELETE", { expectedVersion }, parseArchive, ctx);

export const listVersions = (type: ItemType, id: string, cursor?: string | null) =>
  fetchJson(apiUrl(`/research/${collection(type)}/${enc(id)}/versions${listQuery(cursor)}`), parsePage(parseVersionSummary));

export const getVersion = (type: ItemType, id: string, versionId: string) =>
  fetchJson(apiUrl(`/research/${collection(type)}/${enc(id)}/versions/${enc(versionId)}`), parseVersion);

// ---- 관계 ----

export interface RelationInput {
  source: { type: ItemType; id: string };
  target: { type: ItemType; id: string };
  kind: string;
  description: string;
  directed: boolean;
}

export const listItemRelations = (type: ItemType, id: string, cursor?: string | null) =>
  fetchJson(apiUrl(`/research/${collection(type)}/${enc(id)}/relations${listQuery(cursor)}`), parsePage(parseRelation));

export const createRelation = (input: RelationInput, ctx: MutationContext) =>
  mutate("/research/relations", "POST", input, parseRelation, ctx);

export const updateRelation = (
  id: string,
  input: { kind: string; description: string },
  expectedVersion: number,
  ctx: MutationContext
) => mutate(`/research/relations/${enc(id)}`, "PATCH", { ...input, expectedVersion }, parseRelation, ctx);

export const deleteRelation = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/relations/${enc(id)}`, "DELETE", { expectedVersion }, parseArchive, ctx);

// ---- 공개 (정책 확정 전에는 서버가 쓰기를 503 policy_pending으로 거절) ----

export const publishDocument = (
  id: string,
  input: { versionId: string; materialVersionIds: string[] },
  expectedVersion: number,
  ctx: MutationContext
) =>
  mutate(`/research/documents/${enc(id)}/publications`, "POST", { ...input, expectedVersion }, parsePublicSnapshot, ctx);

export const revokePublication = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/documents/${enc(id)}/publication`, "DELETE", { expectedVersion }, (v) => (isObj(v) ? v : null), ctx);

export const listPublicDocuments = (cursor?: string | null) =>
  fetchJson(apiUrl(`/public/documents${listQuery(cursor)}`), parsePage(parsePublicSummary));

export const getPublicDocument = (documentId: string) =>
  fetchJson(apiUrl(`/public/documents/${enc(documentId)}`), parsePublicSnapshot);

// ---- 개인 Codex 세션 ----

export const getCodexStatus = () => fetchJson(apiUrl("/research/codex/status"), parseCodexStatus);

export const listSessions = (cursor?: string | null) =>
  fetchJson(apiUrl(`/research/sessions${listQuery(cursor)}`), parsePage(parseSessionSummary));

export const getSession = (id: string) => fetchJson(apiUrl(`/research/sessions/${enc(id)}`), parseSessionDetail);

export const getSessionItemRaw = (id: string, itemId: string) =>
  fetchJson(apiUrl(`/research/sessions/${enc(id)}/items/${enc(itemId)}`), parseRawItem);

export const createSession = (input: { title: string; publicDocumentId?: string }, ctx: MutationContext) =>
  mutate("/research/sessions", "POST", input, parseSessionSummary, ctx);

export const sendSessionMessage = (id: string, text: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/sessions/${enc(id)}/messages`, "POST", { text, expectedVersion }, parseSessionSummary, ctx);

export const archiveSession = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/sessions/${enc(id)}`, "DELETE", { expectedVersion }, parseArchive, ctx);

// ---- 오류 안내 ----

/** 계약의 오류 코드를 사용자 안내 문구로 바꾼다. 알 수 없는 실패는 null(호출 측이 일반 문구 사용). */
export function researchErrorMessage(failure: FetchFailure): string | null {
  if (failure.kind !== "http") return null;
  switch (failure.code) {
    case "unauthenticated":
      return "로그인이 필요합니다. 다시 로그인해 주세요.";
    case "forbidden":
      return "이 기능을 이용할 권한이 없습니다.";
    case "csrf_invalid":
    case "origin_not_allowed":
      return "요청을 확인하지 못했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.";
    case "not_found":
      return "항목을 찾을 수 없습니다.";
    case "conflict":
      return "다른 곳에서 먼저 변경되었습니다. 최신 내용을 다시 불러온 뒤 시도해 주세요.";
    case "duplicate":
      return "같은 관계가 이미 있습니다.";
    case "idempotency_conflict":
      return "이전 요청과 충돌했습니다. 다시 시도해 주세요.";
    case "validation_error":
      return "입력값을 확인해 주세요.";
    case "policy_pending":
      return "이용 정책이 확정되기 전이라 아직 사용할 수 없습니다.";
    case "codex_unavailable":
      return "Codex에 연결할 수 없어 대화를 진행할 수 없습니다. 이전 기록은 보존됩니다.";
    case "codex_rejected":
      return "Codex가 이 요청을 받지 않았습니다. 입력은 대화에 보존되어 있으며 새 메시지로 다시 보낼 수 있습니다.";
    case "not_ready":
      return "서비스가 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.";
    default:
      return null;
  }
}
