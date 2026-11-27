import { apiUrl, fetchJson } from "@/lib/api/client";
import { BASE_PATH } from "@/lib/constants";
import { DETAIL_PATH } from "@/lib/routes";

// api-contracts/issue97-agent-research-r2-v1.md. 이 endpoint의 DTO만 연구 작업 기록의 근거다.
type Resource = "material" | "document" | "post";
export interface SavedResearchVersion {
  resourceType: Resource;
  id: string;
  title: string;
  versionId: string;
  versionNumber: number;
  destination: { path: string; resourceId: string; versionId: string };
}
export interface ResearchCall {
  id: string;
  requestId: string;
  operation: string;
  status: "succeeded" | "failed";
  at: string;
  code?: string;
  saved?: SavedResearchVersion;
  target?: { type?: Resource | "relation"; id?: string; versionId?: string; version?: number; postId?: string; draftId?: string; materialId?: string };
  count?: number;
  snapshot?: { type: "public_document" | "public_post"; resourceId: string; snapshotId?: string; versionId?: string };
}

type Obj = Record<string, unknown>;
const obj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const positive = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) > 0;
const resource = (v: unknown): v is Resource => v === "material" || v === "document" || v === "post";
const PATHS: Record<Resource, string> = { material: "/research/material/", document: "/research/document/", post: "/write/" };
const SAVES: Record<string, Resource> = {
  research_material_save: "material", research_material_update: "material",
  research_document_save: "document", research_document_update: "document", research_post_save: "post",
};
const LABELS: Record<string, string> = {
  research_list: "자료·문서·글 목록 조회", research_get: "자료·문서·글 조회",
  research_versions: "버전 목록 조회", research_version_get: "버전 조회", research_relations: "연결 관계 조회",
  research_material_save: "자료 저장", research_material_update: "자료 수정",
  research_document_save: "문서 저장", research_document_update: "문서 수정", research_post_save: "글 저장",
  research_relation_save: "연결 관계 저장", research_relation_update: "연결 관계 수정", research_archive: "보관",
  research_post_draft_get: "글 초안 조회", research_page_context: "공개 글·참고자료 조회",
};
const own = <T,>(v: Record<string, T>, key: string): T | undefined => Object.hasOwn(v, key) ? v[key] : undefined;

function parseSaved(v: unknown, operation: string): SavedResearchVersion | null {
  if (!obj(v) || !resource(v.resourceType) || own(SAVES, operation) !== v.resourceType || !uuid(v.id) || typeof v.title !== "string" || !uuid(v.versionId) || !positive(v.versionNumber)) return null;
  const d = v.destination;
  if (!obj(d) || d.path !== PATHS[v.resourceType] || d.resourceId !== v.id || d.versionId !== v.versionId) return null;
  return {
    resourceType: v.resourceType, id: v.id, title: v.title, versionId: v.versionId, versionNumber: v.versionNumber,
    destination: { path: PATHS[v.resourceType], resourceId: v.id, versionId: v.versionId },
  };
}

function parseCall(v: unknown): ResearchCall | null {
  if (!obj(v) || !uuid(v.id) || !uuid(v.requestId) || typeof v.operation !== "string" || !["succeeded", "failed"].includes(v.status as string) || typeof v.at !== "string" || !Number.isFinite(Date.parse(v.at))) return null;
  const call: ResearchCall = { id: v.id, requestId: v.requestId, operation: v.operation, status: v.status as ResearchCall["status"], at: v.at };
  if (call.status === "succeeded" && own(SAVES, call.operation) && v.saved === undefined) return null;
  if (v.saved !== undefined) {
    const saved = parseSaved(v.saved, call.operation);
    if (call.status !== "succeeded" || !saved) return null;
    call.saved = saved;
  }
  if (call.status === "failed" && typeof v.code === "string" && own(FAILURE_TEXT, v.code)) call.code = v.code;
  if (v.count !== undefined) {
    if (!Number.isSafeInteger(v.count) || (v.count as number) < 0) return null;
    call.count = v.count as number;
  }
  if (v.target !== undefined) {
    if (!obj(v.target)) return null;
    const target: NonNullable<ResearchCall["target"]> = {};
    if (v.target.type !== undefined) {
      if (!resource(v.target.type) && v.target.type !== "relation") return null;
      target.type = v.target.type;
    }
    for (const key of ["id", "versionId", "postId", "draftId", "materialId"] as const) {
      if (v.target[key] === undefined) continue;
      if (!uuid(v.target[key])) return null;
      target[key] = v.target[key];
    }
    if (v.target.version !== undefined) {
      if (!positive(v.target.version)) return null;
      target.version = v.target.version;
    }
    call.target = target;
  }
  if (v.snapshot !== undefined) {
    const s = v.snapshot;
    if (!obj(s) || (s.type !== "public_document" && s.type !== "public_post") || !uuid(s.resourceId)) return null;
    call.snapshot = { type: s.type, resourceId: s.resourceId };
    for (const key of ["snapshotId", "versionId"] as const) {
      if (s[key] === undefined) continue;
      if (!uuid(s[key])) return null;
      call.snapshot[key] = s[key];
    }
  }
  // turnId, args, stdout, provider 정보 등은 클라이언트 DTO로 옮기지 않는다.
  return call;
}

export function parseResearchCalls(v: unknown): ResearchCall[] | null {
  if (!obj(v) || !Array.isArray(v.items)) return null;
  const seen = new Set<string>();
  const calls: ResearchCall[] = [];
  for (const value of v.items) {
    const call = parseCall(value);
    if (!call) return null;
    const key = call.id.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    calls.push(call);
  }
  return calls;
}

export const getResearchCalls = (id: string) => fetchJson(apiUrl(`/research/sessions/${encodeURIComponent(id)}/research-calls`), parseResearchCalls);

export function researchCallLabel(call: ResearchCall): string {
  return own(LABELS, call.operation) ?? "연구 작업";
}

export function researchCallHref(call: ResearchCall): string | null {
  if (call.status !== "succeeded") return null;
  // 모델이 제공한 URL은 사용하지 않는다. 해석된 receipt의 고정 route + UUID만으로 만든다.
  const saved = call.saved;
  // #119 B2: 화면 주소는 경로형이다(서버 destination.path는 예전 목록 경로 그대로 검사만 한다).
  if (saved) return `${BASE_PATH}${DETAIL_PATH[saved.resourceType](saved.id)}#version-history`;
  const t = call.target;
  if (!call.snapshot && t?.type && resource(t.type) && t.id) return `${BASE_PATH}${DETAIL_PATH[t.type](t.id)}`;
  return null;
}

export interface ResearchCallGroup { call: ResearchCall; confirmations: number }
export function groupResearchCalls(calls: ResearchCall[]): ResearchCallGroup[] {
  const seen = new Set<string>();
  const saves = new Map<string, ResearchCallGroup>();
  const rows: ResearchCallGroup[] = [];
  for (const call of calls) {
    if (seen.has(call.id.toLowerCase())) continue;
    seen.add(call.id.toLowerCase());
    const key = call.saved ? `${call.saved.resourceType}:${call.saved.id}:${call.saved.versionId}`.toLowerCase() : null;
    const existing = key ? saves.get(key) : undefined;
    if (existing) { existing.confirmations++; continue; }
    const row = { call, confirmations: 1 };
    rows.push(row);
    if (key) saves.set(key, row);
  }
  return rows;
}

const FAILURE_TEXT: Record<string, string> = {
  conflict: "내용이나 버전이 바뀌어 처리하지 못했습니다. 최신 내용을 확인하세요.",
  idempotency_conflict: "이전 요청과 내용이 달라 처리하지 못했습니다.",
  validation_error: "입력 형식을 확인하지 못했습니다.",
  not_found: "대상을 찾을 수 없습니다.", forbidden: "이 작업을 수행할 권한이 없습니다.",
  not_ready: "작업을 처리할 준비가 되지 않았습니다.",
};
export const researchCallFailure = (code?: string) => own(FAILURE_TEXT, code ?? "") ?? "작업을 완료하지 못했습니다. 현재 내용을 확인하세요.";
