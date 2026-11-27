import { apiUrl, fetchJson, type FetchFailure, type FetchResult } from "@/lib/api/client";
import { own } from "@/lib/research/own";
import { parseBibliography } from "@/lib/research/bibliography";
import { parseRelationKinds } from "@/lib/research/relationKinds";
import {
  CONTENT_KINDS,
  type ArchiveResult,
  type CodexStatus,
  type ContentKind,
  type DocumentSummary,
  type Draft,
  type Endpoint,
  LIMITS,
  type Proposal,
  type AnyDocumentProposal,
  type Bibliography,
  type Evidence,
  type ItemType,
  type Material,
  type MaterialSummary,
  type MessageItem,
  type NoticeItem,
  type AdminAccount,
  type AcceptedScope,
  type PageDescriptor,
  type ToolCallItem,
  type PreviewMaterial,
  type PublicationPreview,
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

const isSummary = (v: unknown): v is string | null => v === null || isStr(v);

// #105 목록에만 있는 활성 관계 수. 없으면 생략, 있으면 0 이상 정수여야 한다.
function relationCountOf(v: Obj): { relationCount?: number } | null {
  if (v.relationCount === undefined) return {};
  return isInt(v.relationCount) && v.relationCount >= 0 ? { relationCount: v.relationCount } : null;
}

function parseMaterialSummary(v: unknown): MaterialSummary | null {
  if (!isObj(v) || v.type !== "material") return null;
  const base = parseItemBase(v);
  if (!base || !isStr(v.sourceUrl) || !isStr(v.collectedAt) || !isKind(v.contentKind) || !isSummary(v.summary)) return null;
  const bibliography = parseBibliography(v.bibliography);
  const count = relationCountOf(v);
  if (bibliography === undefined || !count) return null;
  return {
    ...base,
    type: "material",
    sourceUrl: v.sourceUrl,
    collectedAt: v.collectedAt,
    contentKind: v.contentKind,
    summary: v.summary,
    bibliography,
    ...count,
  };
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
  const count = relationCountOf(v);
  if (!base || publication === undefined || !isSummary(v.summary) || !count) return null;
  return { ...base, type: "document", publication, summary: v.summary, ...count };
}

function parseDocument(v: unknown): ResearchDocument | null {
  const summary = parseDocumentSummary(v);
  if (!summary || !isObj(v) || !isStr(v.content)) return null;
  return { ...summary, content: v.content };
}

function parseVersionSummary(v: unknown): VersionSummary | null {
  if (!isObj(v) || !isId(v.id) || !isId(v.itemId) || !isInt(v.number) || !isStr(v.title) || !isStr(v.createdAt) || !isSummary(v.summary)) {
    return null;
  }
  const out: VersionSummary = { id: v.id, itemId: v.itemId, number: v.number, title: v.title, createdAt: v.createdAt, summary: v.summary };
  if (isStr(v.sourceUrl)) out.sourceUrl = v.sourceUrl;
  if (isStr(v.collectedAt)) out.collectedAt = v.collectedAt;
  if (isKind(v.contentKind)) out.contentKind = v.contentKind;
  // 자료 버전에만 있다
  if (v.bibliography !== undefined) {
    const bibliography = parseBibliography(v.bibliography);
    if (bibliography === undefined) return null;
    out.bibliography = bibliography;
  }
  return out;
}

function parseVersion(v: unknown): Version | null {
  const summary = parseVersionSummary(v);
  if (!summary || !isObj(v) || !isStr(v.content)) return null;
  return { ...summary, content: v.content };
}

const isNullableId = (v: unknown): v is string | null => v === null || isId(v);

function parseDraft(v: unknown): Draft | null {
  if (!isObj(v) || !isId(v.id) || !isNullableId(v.documentId) || !isInt(v.revision) || !isStr(v.title) || !isStr(v.updatedAt)) return null;
  if (!(v.baseDocumentVersion === null || isInt(v.baseDocumentVersion)) || !isNullableId(v.baseVersionId)) return null;
  if (v.state !== "active" && v.state !== "committed" && v.state !== "discarded") return null;
  if (v.content !== undefined && !isStr(v.content)) return null;
  return {
    id: v.id,
    documentId: v.documentId,
    revision: v.revision,
    baseDocumentVersion: v.baseDocumentVersion,
    baseVersionId: v.baseVersionId,
    title: v.title,
    ...(isStr(v.content) ? { content: v.content } : {}),
    updatedAt: v.updatedAt,
    state: v.state,
  };
}

function parseProposal(v: unknown): Proposal | null {
  if (!isObj(v) || !isId(v.id) || !isId(v.documentId) || !isInt(v.version) || !isInt(v.baseDocumentVersion) || !isId(v.baseVersionId)) return null;
  if (!isStr(v.title) || !isStr(v.createdAt) || !isStr(v.updatedAt) || !isNullableId(v.acceptedVersionId)) return null;
  if (v.status !== "pending" && v.status !== "accepted" && v.status !== "discarded") return null;
  if (v.content !== undefined && !isStr(v.content)) return null;
  return {
    id: v.id,
    documentId: v.documentId,
    version: v.version,
    baseDocumentVersion: v.baseDocumentVersion,
    baseVersionId: v.baseVersionId,
    title: v.title,
    ...(isStr(v.content) ? { content: v.content } : {}),
    status: v.status,
    acceptedVersionId: v.acceptedVersionId,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
  };
}

function parseDocumentDraft(v: unknown): { draft: Draft | null } | null {
  if (!isObj(v) || !("draft" in v)) return null;
  if (v.draft === null) return { draft: null };
  const draft = parseDraft(v.draft);
  return draft ? { draft } : null;
}

function parseCommit(v: unknown): { draft: Draft; document: ResearchDocument } | null {
  if (!isObj(v)) return null;
  const draft = parseDraft(v.draft);
  const document = parseDocument(v.document);
  return draft && document ? { draft, document } : null;
}

function parseAnyProposal(v: unknown): AnyDocumentProposal | null {
  if (!isObj(v) || !isId(v.id) || !isNullableId(v.documentId) || !isInt(v.version)) return null;
  if ((v.baseDocumentVersion !== null && !isInt(v.baseDocumentVersion)) || !isNullableId(v.baseVersionId) || !isNullableId(v.acceptedDocumentId)) return null;
  if (!isStr(v.title) || !isStr(v.createdAt) || !isStr(v.updatedAt) || !isNullableId(v.acceptedVersionId)) return null;
  if (v.status !== "pending" && v.status !== "accepted" && v.status !== "discarded") return null;
  if (v.content !== undefined && !isStr(v.content)) return null;
  return {
    id: v.id,
    documentId: v.documentId,
    version: v.version,
    baseDocumentVersion: v.baseDocumentVersion,
    baseVersionId: v.baseVersionId,
    title: v.title,
    ...(isStr(v.content) ? { content: v.content } : {}),
    status: v.status,
    acceptedVersionId: v.acceptedVersionId,
    acceptedDocumentId: v.acceptedDocumentId,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
  };
}

function parseAccept(v: unknown): { proposal: Proposal; document: ResearchDocument; draft: Draft | null } | null {
  if (!isObj(v)) return null;
  const proposal = parseProposal(v.proposal);
  const document = parseDocument(v.document);
  const draft = v.draft === null || v.draft === undefined ? null : parseDraft(v.draft);
  if (!proposal || !document || (v.draft != null && !draft)) return null;
  return { proposal, document, draft };
}

function parseEndpoint(v: unknown): Endpoint | null {
  if (!isObj(v) || !isItemType(v.type) || !isId(v.id) || !isStr(v.title)) return null;
  return { type: v.type, id: v.id, title: v.title };
}

const isNullableText = (v: unknown): v is string | null => v === null || isStr(v);

function parseEvidence(v: unknown): Evidence | null {
  if (!isObj(v) || !isId(v.itemId) || !isId(v.versionId) || !isInt(v.versionNumber) || !isNullableText(v.locator) || !isNullableText(v.quote))
    return null;
  return { itemId: v.itemId, versionId: v.versionId, versionNumber: v.versionNumber, locator: v.locator, quote: v.quote };
}

function parseRelation(v: unknown): Relation | null {
  if (!isObj(v) || !Array.isArray(v.evidence)) return null;
  const source = parseEndpoint(v.source);
  const target = parseEndpoint(v.target);
  const evidence = v.evidence.map(parseEvidence);
  if (
    !source ||
    !target ||
    evidence.some((e) => e === null) ||
    typeof v.kindKnown !== "boolean" ||
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
    kindKnown: v.kindKnown,
    description: v.description,
    directed: v.directed,
    evidence: evidence as Evidence[],
    version: v.version,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
  };
  if (v.direction === "outgoing" || v.direction === "incoming" || v.direction === "bidirectional") {
    out.direction = v.direction;
  }
  return out;
}

export function parsePage<T>(parse: (v: unknown) => T | null) {
  return (body: unknown): Page<T> | null => {
    if (!isObj(body) || !Array.isArray(body.items)) return null;
    if (body.nextCursor !== null && !isStr(body.nextCursor)) return null;
    const items: T[] = [];
    for (const raw of body.items) {
      const item = parse(raw);
      if (item === null) return null;
      items.push(item);
    }
    const page: Page<T> = { items, nextCursor: body.nextCursor };
    if (isInt(body.total) && body.total >= 0) page.total = body.total;
    return page;
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
  if (!isStr(v.content)) return null;
  const out: PublishedMaterial = {
    id: v.id,
    versionId: v.versionId,
    title: v.title,
    sourceUrl: v.sourceUrl,
    collectedAt: v.collectedAt,
    contentKind: v.contentKind,
    content: v.content,
  };
  if (isInt(v.number)) out.number = v.number;
  return out;
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

function parsePreviewMaterial(v: unknown): PreviewMaterial | null {
  if (
    !isObj(v) ||
    !isId(v.id) ||
    !isId(v.versionId) ||
    !isInt(v.number) ||
    !isStr(v.title) ||
    !isStr(v.sourceUrl) ||
    !isStr(v.collectedAt) ||
    !isKind(v.contentKind) ||
    !isStr(v.content) ||
    typeof v.archived !== "boolean" ||
    !Array.isArray(v.relations) ||
    !v.relations.every((r) => isObj(r) && isId(r.id) && isInt(r.version))
  ) {
    return null;
  }
  return {
    id: v.id,
    versionId: v.versionId,
    number: v.number,
    title: v.title,
    sourceUrl: v.sourceUrl,
    collectedAt: v.collectedAt,
    contentKind: v.contentKind,
    content: v.content,
    archived: v.archived,
    relations: (v.relations as Obj[]).map((r) => ({ id: r.id as string, version: r.version as number })),
  };
}

function parsePreview(v: unknown): PublicationPreview | null {
  if (
    !isObj(v) ||
    !isId(v.documentId) ||
    !isId(v.versionId) ||
    !isStr(v.title) ||
    !isStr(v.content) ||
    !isInt(v.expectedVersion) ||
    !isId(v.previewToken) ||
    typeof v.publishable !== "boolean" ||
    !Array.isArray(v.materials)
  ) {
    return null;
  }
  const materials = v.materials.map(parsePreviewMaterial);
  if (materials.some((m) => m === null)) return null;
  return {
    documentId: v.documentId,
    versionId: v.versionId,
    title: v.title,
    content: v.content,
    expectedVersion: v.expectedVersion,
    previewToken: v.previewToken,
    publishable: v.publishable,
    materials: materials as PreviewMaterial[],
  };
}

function parseAdminAccount(v: unknown): AdminAccount | null {
  if (
    !isObj(v) ||
    !isId(v.accountId) ||
    !isStr(v.githubId) ||
    !isStr(v.login) ||
    (v.role !== "admin" && v.role !== "editor" && v.role !== "commenter") ||
    typeof v.isApproved !== "boolean" ||
    !isStr(v.createdAt) ||
    !isStr(v.updatedAt) ||
    !isId(v.version)
  ) {
    return null;
  }
  return {
    accountId: v.accountId,
    githubId: v.githubId,
    login: v.login,
    role: v.role,
    isApproved: v.isApproved,
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
    version: v.version,
  };
}

function parseCodexStatus(v: unknown): CodexStatus | null {
  if (!isObj(v) || typeof v.available !== "boolean") return null;
  if (v.reason !== null && !isStr(v.reason)) return null;
  if (v.verification !== "unverified" && v.verification !== "fixture" && v.verification !== "real") return null;
  if (v.model !== undefined && v.model !== null && !isStr(v.model)) return null;
  const out: CodexStatus = { available: v.available, reason: v.reason, verification: v.verification };
  if (isStr(v.model) && v.model !== "") out.model = v.model;
  // 표시 전용 선택 필드. 형식이 맞지 않으면 버리고 나머지 상태는 그대로 쓴다(접수 판정은 available과 서버가 한다).
  if (isStr(v.accessExpiresAt) && !Number.isNaN(Date.parse(v.accessExpiresAt))) out.accessExpiresAt = v.accessExpiresAt;
  if (isInt(v.remainingAdmissions) && v.remainingAdmissions >= 0) out.remainingAdmissions = v.remainingAdmissions;
  // 범위 밖·정수가 아닌 안내는 버린다(front가 정한 기본 간격을 쓴다).
  if (isInt(v.retryAfterSeconds) && v.retryAfterSeconds >= 1 && v.retryAfterSeconds <= 60) out.retryAfterSeconds = v.retryAfterSeconds;
  return out;
}

// 표시 전용 범위 정보. 형식이 맞지 않으면 버리고 나머지 기록은 그대로 쓴다(권한 판단은 서버가 한다).
function parseScope(v: Obj): AcceptedScope | undefined {
  const p = v.page;
  if (!isObj(p) || !isStr(p.kind) || !isStr(v.capability) || !Array.isArray(v.allowedTools) || !v.allowedTools.every(isStr)) return undefined;
  const page: AcceptedScope["page"] = { kind: p.kind };
  if (isId(p.resourceId)) page.resourceId = p.resourceId;
  if (isStr(p.title)) page.title = p.title;
  return { page, capability: v.capability, allowedTools: v.allowedTools };
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
  const out: SessionSummary = { id: v.id, title: v.title, state: v.state, createdAt: v.createdAt, updatedAt: v.updatedAt, version: v.version };
  if (v.context !== undefined && v.context !== null) {
    const c = v.context;
    if (!isObj(c) || !isId(c.documentId) || !isId(c.publicationId) || !isStr(c.title)) return null;
    out.context = { documentId: c.documentId, publicationId: c.publicationId, title: c.title };
  }
  const scope = parseScope(v);
  if (scope) out.scope = scope;
  if (isStr(v.requestId) && UUID_LOWER.test(v.requestId)) out.requestId = v.requestId;
  // #119 N4(issue119 §N4): 보관 여부. 이전 API에는 없다.
  if (typeof v.archived === "boolean") out.archived = v.archived;
  return out;
}

const UUID_LOWER = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function parseSessionItem(v: unknown): SessionItem | null {
  if (!isObj(v) || !isId(v.id)) return null;
  if (v.type === "message" && (v.role === "user" || v.role === "assistant") && isStr(v.text)) {
    const item: MessageItem = { id: v.id, type: "message", role: v.role, text: v.text };
    if (v.source !== undefined) {
      if (v.source !== "platform" || (v.status !== "pending" && v.status !== "not_recorded")) return null;
      item.source = "platform";
      item.status = v.status;
    }
    if (v.modelMismatch === true) item.modelMismatch = true;
    if (v.role === "user") {
      const scope = parseScope(v);
      if (scope) item.scope = scope;
    }
    if (v.turnStatus === "interrupted") item.turnStatus = "interrupted";
    if (v.partial === true) item.partial = true;
    if (v.displayOnly === true) item.displayOnly = true;
    return item;
  }
  if (v.type === "tool_call" && isStr(v.name) && isStr(v.status)) {
    const tool: ToolCallItem = { id: v.id, type: "tool_call", name: v.name, input: v.input, output: v.output, status: v.status };
    if (isObj(v.error) && isStr(v.error.code)) tool.error = { code: v.error.code };
    if (v.modelMismatch === true) tool.modelMismatch = true;
    if (v.turnStatus === "interrupted") tool.turnStatus = "interrupted";
    return tool;
  }
  if (v.type === "notice" && isStr(v.code)) {
    const notice: NoticeItem = { id: v.id, type: "notice", code: v.code };
    if (v.turnStatus === "interrupted") notice.turnStatus = "interrupted";
    return notice;
  }
  // #90: 알려진 종류(message·tool_call·notice)가 형식에 맞지 않으면 지금처럼 거른다. 새 종류는 대화 전체를 실패로 만들지 않는다.
  if (isStr(v.type) && v.type !== "message" && v.type !== "tool_call" && v.type !== "notice") {
    return { id: v.id, type: "unknown", originalType: v.type };
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

export function mutate<T>(
  path: `/${string}`,
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  body: unknown,
  parse: (v: unknown) => T | null,
  ctx: MutationContext,
  extraHeaders?: Record<string, string>
): Promise<FetchResult<T>> {
  return fetchJson(apiUrl(path), parse, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": ctx.csrfToken,
      "Idempotency-Key": ctx.idempotencyKey,
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });
}

export function listQuery(cursor?: string | null, extra?: Record<string, string>): string {
  const params = new URLSearchParams({ limit: "30", ...extra });
  if (cursor) params.set("cursor", cursor);
  return `?${params.toString()}`;
}

const enc = encodeURIComponent;
const collection = (type: ItemType) => (type === "material" ? "materials" : "documents");

// ---- 자료·문서 ----

// #105 summary·bibliography: 생성에서 생략 = null, 수정에서 생략 = 최신 값을 이어 받음, null = 지움.
export interface MaterialInput {
  title: string;
  sourceUrl: string;
  collectedAt: string;
  contentKind: ContentKind;
  content: string;
  summary?: string | null;
  bibliography?: Bibliography | null;
}

export interface DocumentInput {
  title: string;
  content: string;
  summary?: string | null;
}

// #119 I2(issue119 §I2): q = 최신 제목(자료는 원본 주소도) 부분 일치, sort = updated(기본)|created|title|domain(자료만).
// cursor는 같은 sort에서만 이어 쓸 수 있다(바꾸면 처음부터).
export type ItemSort = "updated" | "created" | "title" | "domain";
export interface ItemListOptions {
  q?: string;
  sort?: ItemSort;
}

const itemQuery = (archived: boolean, opts: ItemListOptions) => ({
  ...(archived ? { archived: "true" } : {}),
  ...(opts.q ? { q: opts.q } : {}),
  ...(opts.sort && opts.sort !== "updated" ? { sort: opts.sort } : {}),
});

export const listMaterials = (cursor?: string | null, archived = false, opts: ItemListOptions = {}) =>
  fetchJson(apiUrl(`/research/materials${listQuery(cursor, itemQuery(archived, opts))}`), parsePage(parseMaterialSummary));

export const listDocuments = (cursor?: string | null, archived = false, opts: Omit<ItemListOptions, "sort"> & { sort?: Exclude<ItemSort, "domain"> } = {}) =>
  fetchJson(apiUrl(`/research/documents${listQuery(cursor, itemQuery(archived, opts))}`), parsePage(parseDocumentSummary));

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

// ---- #22 서버 초안·Codex 제안 ----

export interface DraftInput {
  title: string;
  content: string;
}

/** 새 초안. 기존 문서면 base(문서 version·현재 snapshot ID)를 함께 보낸다. */
export const createDraft = (
  input: DraftInput & { documentId?: string; baseDocumentVersion?: number; baseVersionId?: string },
  ctx: MutationContext
) => mutate("/research/document-drafts", "POST", input, parseDraft, ctx);
export const listDrafts = (cursor?: string | null) =>
  fetchJson(apiUrl(`/research/document-drafts${listQuery(cursor)}`), parsePage(parseDraft));
export const getDraft = (id: string) => fetchJson(apiUrl(`/research/document-drafts/${enc(id)}`), parseDraft);
export const getDocumentDraft = (documentId: string) =>
  fetchJson(apiUrl(`/research/documents/${enc(documentId)}/draft`), parseDocumentDraft);
export const saveDraft = (id: string, input: DraftInput, expectedDraftRevision: number, ctx: MutationContext) =>
  mutate(`/research/document-drafts/${enc(id)}`, "PUT", { ...input, expectedDraftRevision }, parseDraft, ctx);
export const discardDraft = (id: string, expectedDraftRevision: number, ctx: MutationContext) =>
  mutate(`/research/document-drafts/${enc(id)}`, "DELETE", { expectedDraftRevision }, parseDraft, ctx);
/** 명시 저장: 버전 1개를 만든다. 기존 문서는 expectedVersion 필수, base가 바뀌었으면 비교 뒤 현재 snapshot ID를 resolvedBaseVersionId로. */
export const commitDraft = (
  id: string,
  // #105 summary: 생략 = 최신 값을 이어 받음(새 문서는 null), null = 지움. 초안 본문에는 summary가 없다.
  body: { expectedDraftRevision: number; expectedVersion?: number; resolvedBaseVersionId?: string; summary?: string | null },
  ctx: MutationContext
) => mutate(`/research/document-drafts/${enc(id)}/commit`, "POST", body, parseCommit, ctx);

export const listProposals = (documentId: string, cursor?: string | null) =>
  fetchJson(apiUrl(`/research/documents/${enc(documentId)}/proposals${listQuery(cursor)}`), parsePage(parseProposal));
export const getProposal = (documentId: string, proposalId: string) =>
  fetchJson(apiUrl(`/research/documents/${enc(documentId)}/proposals/${enc(proposalId)}`), parseProposal);
export const acceptProposal = (
  documentId: string,
  proposalId: string,
  body: {
    expectedProposalVersion: number;
    expectedVersion: number;
    resolvedBaseVersionId: string;
    title: string;
    content: string;
    draftId?: string;
    expectedDraftRevision?: number;
  },
  ctx: MutationContext
) => mutate(`/research/documents/${enc(documentId)}/proposals/${enc(proposalId)}/accept`, "POST", body, parseAccept, ctx);
export const discardProposal = (documentId: string, proposalId: string, expectedProposalVersion: number, ctx: MutationContext) =>
  mutate(`/research/documents/${enc(documentId)}/proposals/${enc(proposalId)}/discard`, "POST", { expectedProposalVersion }, parseProposal, ctx);

// #28 D1: 문서 제안 전체(새 문서 제안 포함). 기존 문서 제안은 위 문서별 경로로 수락한다.
export const listDocumentProposals = (cursor?: string | null) =>
  fetchJson(apiUrl(`/research/document-proposals${listQuery(cursor)}`), parsePage(parseAnyProposal));
export const getDocumentProposal = (proposalId: string) =>
  fetchJson(apiUrl(`/research/document-proposals/${enc(proposalId)}`), (v) => {
    const p = parseAnyProposal(v);
    return p && p.content !== undefined ? p : null;
  });
/** 새 문서 제안 수락: 사람이 확인한 제목·본문으로 문서와 첫 버전을 만든다. */
export const acceptNewDocumentProposal = (
  proposalId: string,
  body: { expectedProposalVersion: number; title: string; content: string },
  ctx: MutationContext
) => mutate(`/research/document-proposals/${enc(proposalId)}/accept`, "POST", body, parseAccept, ctx);
export const discardDocumentProposal = (proposalId: string, expectedProposalVersion: number, ctx: MutationContext) =>
  mutate(`/research/document-proposals/${enc(proposalId)}/discard`, "POST", { expectedProposalVersion }, parseAnyProposal, ctx);

// ---- 관계 ----

// #105 근거 위치 입력. locator와 quote 중 하나 이상(각각 공백뿐 금지).
export interface EvidenceInput {
  itemId: string;
  versionId: string;
  locator?: string | null;
  quote?: string | null;
}

// #105 directed는 보내지 않는다(서버가 kind에서 정한다). description(근거 문구)은 필수.
export interface RelationInput {
  source: { type: ItemType; id: string };
  target: { type: ItemType; id: string };
  kind: string;
  description: string;
  evidence?: EvidenceInput[];
}

/** #105 관계 어휘(인증 없음). 화면의 kind 선택지·문구·극성 묶음은 이 응답만 따른다. */
export const getRelationKinds = () => fetchJson(apiUrl("/research/relation-kinds"), parseRelationKinds);

export const listItemRelations = (type: ItemType, id: string, cursor?: string | null) =>
  fetchJson(apiUrl(`/research/${collection(type)}/${enc(id)}/relations${listQuery(cursor)}`), parsePage(parseRelation));

export const createRelation = (input: RelationInput, ctx: MutationContext) =>
  mutate("/research/relations", "POST", input, parseRelation, ctx);

export const updateRelation = (
  id: string,
  // evidence: 생략 = 기존 목록 유지, 보내면 전체 교체, [] = 비움
  input: { kind: string; description: string; evidence?: EvidenceInput[] },
  expectedVersion: number,
  ctx: MutationContext
) => mutate(`/research/relations/${enc(id)}`, "PATCH", { ...input, expectedVersion }, parseRelation, ctx);

export const deleteRelation = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/relations/${enc(id)}`, "DELETE", { expectedVersion }, parseArchive, ctx);

// ---- 공개 (정책 확정 전에는 서버가 쓰기를 503 policy_pending으로 거절) ----

/** 공개 전 미리보기. versionId를 생략하면 최신 문서 버전 기준. */
export const getPublicationPreview = (id: string, versionId?: string) =>
  fetchJson(
    apiUrl(`/research/documents/${enc(id)}/publication-preview${versionId ? `?versionId=${enc(versionId)}` : ""}`),
    parsePreview
  );

/** 미리보기에 보인 문서 버전과 직접 연결 자료 전체를 공개한다(자료는 서버가 도출·고정). */
export const publishDocument = (id: string, preview: PublicationPreview, ctx: MutationContext) =>
  mutate(
    `/research/documents/${enc(id)}/publications`,
    "POST",
    { versionId: preview.versionId, expectedVersion: preview.expectedVersion, previewToken: preview.previewToken },
    parsePublicSnapshot,
    ctx
  );

export const revokePublication = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/documents/${enc(id)}/publication`, "DELETE", { expectedVersion }, (v) => (isObj(v) ? v : null), ctx);

export const listPublicDocuments = (cursor?: string | null) =>
  fetchJson(apiUrl(`/public/documents${listQuery(cursor)}`), parsePage(parsePublicSummary));

export const getPublicDocument = (documentId: string) =>
  fetchJson(apiUrl(`/public/documents/${enc(documentId)}`), parsePublicSnapshot);

// ---- 개인 Codex 세션 ----

export const getCodexStatus = () => fetchJson(apiUrl("/research/codex/status"), parseCodexStatus);

// #10 실행 환경 준비 요청. 200(준비됨)·202(준비·대기 접수) 모두 상태 조회와 같은 본문이다. 모델 질문 접수가 아니다.
export const prepareCodexRunner = (ctx: MutationContext) =>
  mutate("/research/codex/runner", "POST", {}, parseCodexStatus, ctx);

/** archived: #119 N4 보관한 대화만(기본은 보관하지 않은 대화). */
export const listSessions = (cursor?: string | null, archived = false) =>
  fetchJson(apiUrl(`/research/sessions${listQuery(cursor, archived ? { archived: "true" } : undefined)}`), parsePage(parseSessionSummary));

/** #119 N4: 보관한 대화를 되돌린다(owner + CSRF + Idempotency-Key). 404 = 내 보관 대화 아님, 409 = 버전 불일치. */
export const restoreSession = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/sessions/${enc(id)}/restore`, "POST", { expectedVersion }, parseSessionSummary, ctx);

export const getSession = (id: string) => fetchJson(apiUrl(`/research/sessions/${enc(id)}`), parseSessionDetail);

export const getSessionItemRaw = (id: string, itemId: string) =>
  fetchJson(apiUrl(`/research/sessions/${enc(id)}/items/${enc(itemId)}`), parseRawItem);

export const createSession = (input: { title: string; publicDocumentId?: string }, ctx: MutationContext) =>
  mutate("/research/sessions", "POST", input, parseSessionSummary, ctx);

// page는 보내기를 누른 순간의 페이지. 결과 불명 재전송은 처음 body(page 포함)를 그대로 보낸다.
// 이전 보관분처럼 page가 없으면 넣지 않는다(서버는 general로 처리).
export const sendSessionMessage = (
  id: string,
  body: { text: string; expectedVersion: number; page?: PageDescriptor },
  ctx: MutationContext
) => mutate(`/research/sessions/${enc(id)}/messages`, "POST", body, parseSessionSummary, ctx);

// #90 중지(api-contracts/issue90-chat-stream.md §2). 멱등이라 Idempotency-Key가 필요 없다. 서버는 DB에 쓰지 않는다.
export interface InterruptResult {
  requestId: string;
  state: "interrupting" | "not_running";
}
function parseInterrupt(v: unknown): InterruptResult | null {
  if (!isObj(v) || !isStr(v.requestId) || (v.state !== "interrupting" && v.state !== "not_running")) return null;
  return { requestId: v.requestId, state: v.state };
}
export const interruptSession = (id: string, requestId: string, csrfToken: string) =>
  fetchJson(apiUrl(`/research/sessions/${enc(id)}/interrupt`), parseInterrupt, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify({ requestId }),
  });

/** 스트림 runner 이벤트(`/codex/status`와 같은 객체)를 상태 값으로 읽는다. 형식이 맞지 않으면 null. */
export const readCodexStatus = (v: unknown): CodexStatus | null => parseCodexStatus(v);

export const archiveSession = (id: string, expectedVersion: number, ctx: MutationContext) =>
  mutate(`/research/sessions/${enc(id)}`, "DELETE", { expectedVersion }, parseArchive, ctx);

// ---- 오류 안내 ----

const RUNNER_REFUSAL_CODES = new Set([
  "codex_runner_not_prepared",
  "codex_provisioning",
  "codex_capacity_wait",
  "codex_runner_unreachable",
  "codex_runner_failed",
  "codex_runner_stopped",
]);

// #22 409 conflict의 세부 사유(error.details.reason). 다른 409에는 없을 수 있어 일반 문구로 둔다.
const CONFLICT_REASONS: Record<string, string> = {
  active_draft_exists: "이 문서에는 이미 작성 중인 초안이 있습니다. 그 초안을 불러와 이어서 쓰세요.",
  draft_revision: "다른 탭이나 기기에서 이 초안이 먼저 바뀌었습니다. 서버의 초안과 지금 입력 중 하나를 고르세요.",
  draft_closed: "이 초안은 이미 저장되었거나 버려져 더 이상 고칠 수 없습니다. 지금 입력은 화면에 남아 있습니다.",
  document_version: "문서가 그사이 바뀌었습니다. 현재 버전과 비교한 뒤 다시 저장하세요.",
  resource_version: "다른 곳에서 새 버전을 저장했습니다. 최신 내용을 불러와 비교한 뒤 다시 저장하세요.",
  base_changed: "초안을 시작한 뒤 문서가 바뀌었습니다. 현재 버전과 비교한 뒤 다시 저장하세요.",
  archived: "보관한 문서라 저장하거나 수락할 수 없습니다. 입력은 그대로 있습니다.",
  proposal_closed: "이 제안은 이미 수락되었거나 버려졌습니다. 목록을 새로 불러오세요.",
};

// #105 422 validation_error의 details.reason
const VALIDATION_REASONS: Record<string, string> = {
  relation_kind_unknown: "관계 유형을 목록에서 골라 주세요.",
  relation_direction_mismatch: "이 관계의 방향과 맞지 않는 유형입니다. 다른 유형을 골라 주세요.",
  relation_reason_required: "연결한 이유(근거)를 입력해 주세요.",
  evidence_limit_exceeded: `근거 위치는 ${LIMITS.evidence}개까지 넣을 수 있습니다.`,
  evidence_endpoint_mismatch: "근거 위치는 이 관계로 연결된 두 항목에서만 고를 수 있습니다.",
  evidence_version_not_found: "근거로 고른 버전을 찾을 수 없습니다. 목록을 새로 불러온 뒤 다시 골라 주세요.",
};

/** 계약의 오류 코드를 사용자 안내 문구로 바꾼다. 알 수 없는 실패는 null(호출 측이 일반 문구 사용). */
export function researchErrorMessage(failure: FetchFailure): string | null {
  if (failure.kind !== "http") return null;
  // #10 실행 환경 거절 문구는 "보내지 않았습니다"를 확정하므로 정확한 503(접수 전 거절)이나 서버가 확정한
  // 실패 세션의 오류(status 0으로 전달)에만 쓴다. 같은 코드의 500·502 등은 결과 불명이라 일반 안내로 둔다.
  if (RUNNER_REFUSAL_CODES.has(failure.code ?? "") && failure.status !== 503 && failure.status !== 0) return null;
  switch (failure.code) {
    case "unauthenticated":
      return "로그인이 필요합니다. 다시 로그인해 주세요.";
    case "forbidden":
      return "이 기능을 이용할 권한이 없습니다. 개인 자료·문서와 대화 기능은 관리자가 승인한 편집자·관리자만 이용할 수 있습니다.";
    case "csrf_invalid":
    case "origin_not_allowed":
      return "요청을 확인하지 못했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.";
    case "not_found":
      return "항목을 찾을 수 없습니다.";
    case "conflict":
      return own(CONFLICT_REASONS, failure.reason ?? "") ?? "다른 곳에서 먼저 변경되었습니다. 최신 내용을 다시 불러온 뒤 시도해 주세요.";
    case "duplicate":
      return "같은 관계가 이미 있습니다.";
    case "idempotency_conflict":
      return "이전 요청과 충돌했습니다. 다시 시도해 주세요.";
    case "validation_error":
      return own(VALIDATION_REASONS, failure.reason ?? "") ?? "입력값을 확인해 주세요.";
    case "policy_pending":
      return "이용 정책이 확정되기 전이라 아직 사용할 수 없습니다.";
    case "codex_unavailable":
      return "연결 안 됨";
    case "codex_rejected":
      return "이 요청은 처리되지 않았습니다.";
    case "codex_not_enabled_for_account":
      return "이 계정은 대화를 사용할 수 없습니다.";
    // 아래 codex_* 문구는 선접수 거절(503)과 접수 뒤 실행 실패(failed) 모두에 쓰인다.
    case "codex_auth_expired":
      return "서버 인증 만료 · 운영자 확인 필요";
    case "codex_auth_revoked":
      return "서버 인증 해제 · 운영자 확인 필요";
    case "codex_model_unavailable":
      return "고정된 모델을 사용할 수 없습니다.";
    case "codex_policy_refused":
      return "정책에 따라 거부됐습니다.";
    case "codex_budget_exhausted":
      return "사용 한도에 도달했습니다.";
    case "codex_rate_limited":
      return "요청 한도에 도달했습니다.";
    case "codex_failed":
      return "요청이 실패했습니다.";
    // #10 실행 환경 상태로 접수 전에 거절된 질문(초안은 남는다). 자동으로 다시 보내지 않는다.
    case "codex_runner_not_prepared":
      return "환경 준비 필요";
    case "codex_provisioning":
      return "환경 준비 중";
    case "codex_capacity_wait":
      return "사용 대기 중";
    case "codex_runner_unreachable":
      return "연결 확인 중";
    case "codex_runner_failed":
      return "운영자 확인 필요";
    // #16: 쉬는 중과 정리 중(stopping) 모두 이 코드다. 정리 중에는 다시 켜기 버튼이 없으므로 상태 안내를 따르게 한다.
    case "codex_runner_stopped":
      return "환경이 꺼져 있거나 정리 중입니다.";
    // 접수 뒤 실행 중 환경이 중단된 것이 확인된 실패(접수 전 거절 아님).
    case "codex_runner_lost":
      return "실행 환경이 중단됐습니다.";
    case "not_ready":
      return "서비스가 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.";
    default:
      return null;
  }
}

// ---- 관리자 (승인된 admin 전용) ----

export interface AdminAccountFilter {
  // #119 L1·L5(issue119 §L1): 승인 여부, 역할, login 부분 일치
  approved?: boolean;
  role?: AdminAccount["role"];
  q?: string;
}

export const listAdminAccounts = (cursor?: string | null, f: AdminAccountFilter = {}) =>
  fetchJson(
    apiUrl(
      `/admin/accounts${listQuery(cursor, {
        ...(f.approved !== undefined ? { approved: String(f.approved) } : {}),
        ...(f.role ? { role: f.role } : {}),
        ...(f.q ? { q: f.q } : {}),
      })}`
    ),
    parsePage(parseAdminAccount)
  );

export interface AuditEntry {
  id: string;
  createdAt: string;
  actor: { kind: "account"; accountId: string; login: string | null } | { kind: "operator"; label: string };
  account: { accountId: string; login: string; githubId: string };
  reason: string;
  before: { role: string; isApproved: boolean };
  after: { role: string; isApproved: boolean };
}

function parseMembershipState(v: unknown): AuditEntry["before"] | null {
  return isObj(v) && isStr(v.role) && typeof v.isApproved === "boolean" ? { role: v.role, isApproved: v.isApproved } : null;
}

function parseAuditEntry(v: unknown): AuditEntry | null {
  if (!isObj(v) || !isId(v.id) || !isStr(v.createdAt) || !isStr(v.reason) || !isObj(v.actor) || !isObj(v.account)) return null;
  const before = parseMembershipState(v.before);
  const after = parseMembershipState(v.after);
  const a = v.account;
  if (!before || !after || !isId(a.accountId) || !isStr(a.login) || !isStr(a.githubId)) return null;
  const act = v.actor;
  let actor: AuditEntry["actor"];
  if (act.kind === "account" && isId(act.accountId) && (act.login === null || isStr(act.login))) actor = { kind: "account", accountId: act.accountId, login: act.login };
  else if (act.kind === "operator" && isStr(act.label)) actor = { kind: "operator", label: act.label };
  else return null;
  return { id: v.id, createdAt: v.createdAt, actor, account: { accountId: a.accountId, login: a.login, githubId: a.githubId }, reason: v.reason, before, after };
}

export interface UrlMetadata {
  url: string;
  title?: string;
  siteName?: string;
  bibliography: Bibliography | null;
}

function parseUrlMetadata(v: unknown): UrlMetadata | null {
  if (!isObj(v) || !isStr(v.url)) return null;
  const bib = v.bibliography === undefined ? null : parseBibliography(v.bibliography);
  if (bib === undefined) return null;
  return {
    url: v.url,
    ...(isStr(v.title) && v.title ? { title: v.title } : {}),
    ...(isStr(v.siteName) && v.siteName ? { siteName: v.siteName } : {}),
    bibliography: bib,
  };
}

/**
 * #119 K3(issue119 §K3): 출처 URL의 제목·서지정보를 서버가 읽어 준다(저장하지 않는다). 승인 회원 session + CSRF, Idempotency-Key 없음.
 * 몇 초 걸릴 수 있다(서버 예산 5초 + 느린 응답).
 */
export const fetchUrlMetadata = (url: string, csrfToken: string) =>
  fetchJson(
    apiUrl("/research/url-metadata"),
    parseUrlMetadata,
    { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify({ url }) },
    15_000
  );

/** K3 오류 안내 */
export function urlMetadataError(failure: FetchFailure): string {
  if (failure.kind === "http") {
    switch (failure.code) {
      case "url_not_allowed":
        return "이 주소는 가져올 수 없습니다(공개 인터넷의 http·https 주소만 됩니다).";
      case "validation_error":
        return "출처 URL 형식을 확인해 주세요.";
      case "unsupported_content":
        return "웹페이지(HTML)가 아니라 정보를 읽지 못했습니다. 직접 입력해 주세요.";
      case "fetch_failed":
        return "페이지를 불러오지 못했습니다(응답 없음·오류·너무 많은 이동). 직접 입력해 주세요.";
      case "rate_limited":
        return "요청이 많습니다. 잠시 뒤 다시 시도해 주세요.";
      case "not_ready":
        return "이 서버에서는 자동 채우기를 쓸 수 없습니다. 직접 입력해 주세요.";
    }
    if (failure.status === 401 || failure.status === 403) return "로그인·승인 상태를 확인해 주세요.";
  }
  if (failure.kind === "timeout") return "응답이 늦어 멈췄습니다. 다시 시도하거나 직접 입력해 주세요.";
  return "정보를 가져오지 못했습니다. 직접 입력해 주세요.";
}

/** #119 L3(issue119 §L3): 권한 변경 감사 기록(관리자 전용, 최신순, 읽기 전용). */
export const listAdminAudit = (cursor?: string | null, accountId?: string) =>
  fetchJson(apiUrl(`/admin/audit${listQuery(cursor, accountId ? { accountId } : undefined)}`), parsePage(parseAuditEntry));

export const setMembership = (
  accountId: string,
  input: { role: "editor" | "commenter"; isApproved: boolean; reason: string },
  expectedVersion: string,
  ctx: MutationContext
) => mutate(`/admin/accounts/${enc(accountId)}/membership`, "PUT", { ...input, expectedVersion }, parseAdminAccount, ctx);
