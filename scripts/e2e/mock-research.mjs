// M3 모의 API (api-contracts/m3-research-data.md). 화면 검증용 메모리 구현이며 실제 backend·DB·Codex가 아니다.
// 계약의 소유권(타인 ID 404), Origin/CSRF, Idempotency-Key 재생, expectedVersion 충돌, 정책 대기 응답을 흉내 낸다.
import crypto from "node:crypto";
import {
  IMAGE_LIMIT, createPostProposal, handleImageDelete, handleImageRead, handlePostMutation, handlePostRead, handlePublicPosts,
  mediaBytes, newPostDraftOf, postTitle, publicPostTitle, uploadImage,
} from "./mock-posts.mjs";

export const FIXED_MODEL = "gpt-6.1-sol";
// 넓은 표·코드·긴 문자열이 있는 답변(대화창 폭·가로 넘침 검증용). 마지막 열 머리글은 "마지막열"로 끝을 확인한다.
const WIDE_ANSWER = [
  "모의 응답: 비교 표입니다.",
  "",
  "| 항목 | 모델 | 데이터셋 | 정확도 | 지연(ms) | 메모리 | 라이선스 | 출처 링크 | 비고 마지막열 |",
  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
  "| 1 | Transformer-XL | WikiText-103 | 0.912 | 120 | 16GB | Apache-2.0 | https://example.com/papers/transformer-xl/very/long/path/to/the/reference/document | 긴 한글 설명이 들어가는 칸으로 줄바꿈이 필요합니다 |",
  "| 2 | Longformer | arXiv | 0.887 | 98 | 12GB | MIT | https://example.com/b | 표의 마지막 열 내용 |",
  "",
  "```python",
  "result = compare_models(models=[\"transformer-xl\", \"longformer\", \"bigbird\"], dataset=\"wikitext-103\", metrics=[\"accuracy\", \"latency\", \"memory\"], seed=42)",
  "```",
  "",
  "긴 주소: https://example.com/a/really/long/url/that/does/not/contain/any/spaces/and/keeps/going/until/it/would/overflow/the/panel?query=1&other=2",
  "",
  "긴 영문: Pneumonoultramicroscopicsilicovolcanoconiosis_and_another_identifier_without_breaks_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
  "",
  "긴 한글: 띄어쓰기없이아주길게이어지는한글문장이패널폭을넘지않고줄바꿈되는지확인하기위한검증용문자열입니다",
].join("\n");

// 선접수 단계에서 거절되는 사유(POST 503 codex_<reason>)
const GATED_REASONS = ["not_enabled_for_account", "auth_expired", "auth_revoked", "model_unavailable", "policy_refused", "budget_exhausted"];

export const researchMode = {
  // "approved": 승인 계정 허용 / "pending": 모든 개인 API 503 policy_pending
  access: "approved",
  // 확정 정책(0004 이후): 공개 쓰기 허용. "pending"은 이전 정책 대기 상태 재현용.
  publish: "allowed",
  // "unavailable" | "fixture" | "reject"(예약 202 후 runner가 명확히 거절 → failed/codex_rejected)
  codex: "unavailable",
  // "reject": 변경 요청을 403 csrf_invalid로 거절(키 확인 전 단계)
  csrf: "normal",
  // 시험 창: 접수 종료 시각(ms)과 남은 접수 횟수. null이면 표시·제한하지 않는다.
  windowEnd: null,
  remaining: null,
  // 모의 turn 실행 시간(ms). 긴 실행 안내 검증용.
  turnMs: 2500,
};

// 시험 창 상태(fixture 연결에만 적용): 종료 시각이 지나면 auth_expired, 횟수가 0이면 budget_exhausted
function windowReason() {
  if (researchMode.windowEnd !== null && Date.now() >= researchMode.windowEnd) return "auth_expired";
  if (researchMode.remaining !== null && researchMode.remaining <= 0) return "budget_exhausted";
  return null;
}
function windowFields() {
  const out = {};
  if (researchMode.windowEnd !== null) out.accessExpiresAt = new Date(researchMode.windowEnd).toISOString();
  if (researchMode.remaining !== null) out.remainingAdmissions = researchMode.remaining;
  return out;
}

// 접수(202) 뒤 실행 중 인증 실패. back 계약: 만료 → codex_auth_expired, 외부 갱신 요구/401 → codex_auth_revoked.
// 현재 turn의 userMessage가 Codex에 기록됐는지에 따라 입력은 Codex 기록 또는 플랫폼 미기록으로 남는다.
const MIDTURN = {
  midturn_expired: { code: "codex_auth_expired", then: "auth_expired", recorded: false },
  midturn_revoked: { code: "codex_auth_revoked", then: "auth_revoked", recorded: true },
  // #10: 접수 뒤 실행 중 환경 유실이 확정된 실패(입력은 미기록으로 남김). 접수 전 거절 코드가 아니다.
  runner_lost: { code: "codex_runner_lost", then: "fixture", recorded: false },
};

// #10 사용자별 실행 환경(api-contracts/issue10-user-runners-interface-v1.md). initial이 ""이면 기존(시험·정적) 동작이고
// 준비 경로가 없는 이전 API처럼 POST /research/codex/runner는 404다.
// prepare: "ok"(준비 시작 → readyMs 뒤 사용 가능) | "hold"(활성화 중에 머묾) | "fail"(readyMs 뒤 준비 실패)
//          | "capacity"(사용 대기에 머묾) | "403" | "404" | "405" | "500"
// hint: ""면 계약 표의 안내 간격, 그 밖의 값은 그대로 retryAfterSeconds로 보낸다(범위 밖·문자열 검증용).
// 모의 서버의 계정별 합침은 backend 중복 생성 방지의 증거가 아니다.
export const runnerMode = { initial: "", prepare: "ok", readyMs: 2500, hint: "", credHint: "1" };
const runnerStates = new Map(); // owner -> "ready" | reason
const RUNNER_HINT = { provisioning: 2, capacity_wait: 15, runner_unreachable: 5, stopping: 5 };
// #16 운영자 토큰 오류 3종(사용자 환경 health)은 retryAfterSeconds 30을 함께 준다. credHint=0이면 안내 없는 이전 응답.
const CREDENTIAL_HINT = { not_configured: 30, auth_expired: 30, auth_revoked: 30 };
const RUNNER_PREFLIGHT = {
  not_prepared: "codex_runner_not_prepared",
  provisioning: "codex_provisioning",
  capacity_wait: "codex_capacity_wait",
  runner_unreachable: "codex_runner_unreachable",
  runner_failed: "codex_runner_failed",
  // #16: 정리 중(stopping)도 쉬는 중과 같은 확정 미접수 코드다.
  stopping: "codex_runner_stopped",
  idle_stopped: "codex_runner_stopped",
};
const runnerOf = (owner) => runnerStates.get(owner) ?? runnerMode.initial;
/** 모든 계정의 실행 환경 상태를 바꾼다(유휴 회수 등). 빈 값이면 계정별 상태를 지운다. */
export function setRunnerAll(state) {
  if (state === "") runnerStates.clear();
  else for (const k of [...runnerStates.keys()]) runnerStates.set(k, state);
}
function runnerStatus(owner) {
  const st = runnerOf(owner);
  if (st === "ready") return { available: true, reason: null, verification: "fixture", model: FIXED_MODEL };
  const out = { available: false, reason: st, verification: "unverified", model: FIXED_MODEL };
  if (runnerMode.hint !== "") {
    const n = Number(runnerMode.hint);
    out.retryAfterSeconds = Number.isNaN(n) ? runnerMode.hint : n;
  } else if (Object.hasOwn(RUNNER_HINT, st)) out.retryAfterSeconds = RUNNER_HINT[st];
  else if (Object.hasOwn(CREDENTIAL_HINT, st) && runnerMode.credHint !== "0") out.retryAfterSeconds = CREDENTIAL_HINT[st];
  return out;
}
function prepareRunner(owner, b) {
  if (!runnerMode.initial) return fail(404, "not_found", "Not found");
  const m = runnerMode.prepare;
  if (m === "403") return fail(403, "forbidden", "Runner preparation is not permitted");
  if (m === "404") return fail(404, "not_found", "Not found");
  if (m === "405") return fail(405, "method_not_allowed", "Method not allowed");
  if (m === "500") return fail(500, "internal_error", "Internal error");
  if (Object.keys(b).length) return fail(422, "validation_error", "Unknown field");
  const st = runnerOf(owner);
  // 운영자 토큰 오류는 이미 붙은 환경의 health다. 준비 요청은 새 준비를 시작하지 않고 현재 상태를 돌려준다.
  if (st === "ready" || Object.hasOwn(CREDENTIAL_HINT, st)) return ok(200, runnerStatus(owner));
  // #16: 불확실한 실패는 운영자 확인 전까지 새 준비를 시작하지 않는다(상태·증거 그대로).
  if (st === "runner_failed") return fail(409, "runner_reconciliation_required", "Runner state requires reconciliation");
  // 첫 slice: 정리 중 준비 요청은 저장하지 않고 거절한다(backend Refused → 503).
  if (st === "stopping") return fail(503, "codex_runner_unreachable", "Runner state requires reconciliation");
  // 이미 준비 중·대기 중이면 같은 의도로 합친다(새 준비를 시작하지 않음).
  if (st === "provisioning" || st === "capacity_wait") return ok(202, runnerStatus(owner));
  const next = m === "capacity" ? "capacity_wait" : "provisioning";
  runnerStates.set(owner, next);
  if (next === "provisioning" && m !== "hold") {
    setTimeout(() => {
      if (runnerStates.get(owner) === "provisioning") runnerStates.set(owner, m === "fail" ? "runner_failed" : "ready");
    }, runnerMode.readyMs);
  }
  return ok(202, runnerStatus(owner));
}

const items = new Map(); // id -> item
const relations = new Map(); // id -> relation
const sessions = new Map(); // id -> session
const idem = new Map(); // owner:key -> { fp, status, body }
const drafts = new Map(); // #22 id -> draft
const proposals = new Map(); // #22 id -> proposal

const now = () => new Date().toISOString();
const uuid = () => crypto.randomUUID();
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}
const fail = (status, code, message, extra = {}) => ({ status, body: { error: { code, message, ...extra } } });
const ok = (status, body) => ({ status, body });

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return null;
  }
}

// ---- 표현 ----

function latest(item) {
  return item.versions.at(-1);
}
function summary(item) {
  const v = latest(item);
  const base = {
    id: item.id,
    type: item.type,
    title: v.title,
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
    version: item.version,
    latestVersion: { id: v.id, number: v.number, createdAt: v.createdAt },
    archived: item.archived,
  };
  if (item.type === "material") return { ...base, sourceUrl: v.sourceUrl, collectedAt: v.collectedAt, contentKind: v.contentKind };
  return {
    ...base,
    publication: item.publication && {
      id: item.publication.id,
      state: "published",
      versionId: item.publication.versionId,
      publishedAt: item.publication.publishedAt,
      materialIds: item.publication.materials.map((m) => m.id),
      materials: item.publication.materials.map((m) => ({ id: m.id, versionId: m.versionId })),
    },
  };
}
const detail = (item) => ({ ...summary(item), content: latest(item).content });
function versionView(item, v, withContent) {
  const out = { id: v.id, itemId: item.id, number: v.number, title: v.title, createdAt: v.createdAt };
  if (item.type === "material") Object.assign(out, { sourceUrl: v.sourceUrl, collectedAt: v.collectedAt, contentKind: v.contentKind });
  if (withContent) out.content = v.content;
  return out;
}
const endpoint = (ref) => ({ type: ref.type, id: ref.id, title: latest(items.get(ref.id)).title });
function relationView(rel, selfId) {
  const out = {
    id: rel.id,
    source: endpoint(rel.source),
    target: endpoint(rel.target),
    kind: rel.kind,
    description: rel.description,
    directed: rel.directed,
    version: rel.version,
    createdAt: rel.createdAt,
    updatedAt: rel.updatedAt,
  };
  if (selfId) out.direction = !rel.directed ? "bidirectional" : rel.source.id === selfId ? "outgoing" : "incoming";
  return out;
}
function page(list, url) {
  const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 30)));
  const start = Number(url.searchParams.get("cursor") ?? 0);
  const slice = list.slice(start, start + limit);
  return { items: slice, nextCursor: start + limit < list.length ? String(start + limit) : null };
}
const byUpdated = (a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0);

function snapshot(item) {
  const pub = item.publication;
  const v = item.versions.find((x) => x.id === pub.versionId);
  return {
    id: pub.id,
    documentId: item.id,
    versionId: v.id,
    title: v.title,
    content: v.content,
    publishedAt: pub.publishedAt,
    materials: pub.materials.map((m) => ({ ...m.snapshot })),
    author: { login: pub.authorLogin },
  };
}

// ---- 검증 ----

function validateItem(type, b) {
  const fields = [];
  if (typeof b.title !== "string" || !b.title.trim() || b.title.length > 300) fields.push({ path: "body.title", message: "required, <=300" });
  if (typeof b.content !== "string" || b.content.length > 200000) fields.push({ path: "body.content", message: "<=200000" });
  if (type === "material") {
    let urlOk = false;
    try {
      urlOk = ["http:", "https:"].includes(new URL(b.sourceUrl).protocol);
    } catch {}
    if (!urlOk) fields.push({ path: "body.sourceUrl", message: "absolute http/https" });
    if (typeof b.collectedAt !== "string" || Number.isNaN(Date.parse(b.collectedAt)) || !/(Z|[+-]\d\d:\d\d)$/.test(b.collectedAt))
      fields.push({ path: "body.collectedAt", message: "timezone-aware RFC3339" });
    if (!["full", "excerpt", "summary"].includes(b.contentKind)) fields.push({ path: "body.contentKind", message: "invalid" });
  }
  return fields;
}
function newVersion(type, b, number) {
  const v = { id: uuid(), number, title: b.title, content: b.content, createdAt: now() };
  if (type === "material") Object.assign(v, { sourceUrl: b.sourceUrl, collectedAt: b.collectedAt, contentKind: b.contentKind });
  return v;
}
function owned(id, owner, type) {
  const item = items.get(id);
  return item && item.owner === owner && (!type || item.type === type) ? item : null;
}
// #28 글 모의 API에 넘기는 문서 조회(본인 문서만)
const docView = (item) => ({
  id: item.id, title: latest(item).title, version: item.version, latestVersionId: latest(item).id, archived: item.archived, published: !!item.publication,
});
const postDocs = {
  get: (owner, id) => {
    const item = owned(id, owner, "document");
    return item ? docView(item) : null;
  },
  list: (owner) => [...items.values()].filter((i) => i.owner === owner && i.type === "document").sort(byUpdated).map(docView),
};
const stale = (b, current) => !Number.isInteger(b.expectedVersion) || b.expectedVersion !== current;

// ---- 처리 ----

// ---- #22 서버 초안·Codex 제안(api-contracts/issue22-writing-editor.md) ----

const conflict = (reason) => fail(409, "conflict", "The draft, proposal or document changed; compare before retrying", { details: { reason } });
// 소유자는 응답에 싣지 않고, 목록에는 본문을 싣지 않는다.
function writingView(row, withContent = true) {
  const out = { ...row };
  delete out.owner;
  if (!withContent) delete out.content;
  return out;
}
const draftView = writingView;
const proposalView = writingView;
// #28 문서 제안 전체 목록 항목(새 문서 제안은 수락 전 documentId·base가 null)
const anyProposalView = (p, withContent = true) => ({ ...writingView(p, withContent), acceptedDocumentId: p.status === "accepted" ? p.documentId : null });
const activeDraftOf = (owner, documentId) =>
  [...drafts.values()].find((d) => d.owner === owner && d.documentId === documentId && d.state === "active");
function draftFields(b) {
  const fields = [];
  if (typeof b.title !== "string" || b.title.length > 300) fields.push({ path: "body.title", message: "string<=300" });
  if (typeof b.content !== "string" || b.content.length > 200000) fields.push({ path: "body.content", message: "string<=200000" });
  return fields;
}
function changeDocument(item, title, content) {
  item.versions.push(newVersion("document", { title, content }, item.versions.length + 1));
  item.version++;
  item.updatedAt = now();
}

/** 모의 Codex의 research_document_update: 제안만 만들고 문서는 바꾸지 않는다. */
export function createProposal(owner, documentId, title, content) {
  const item = owned(documentId, owner, "document");
  if (!item || item.archived) return null;
  const t = now();
  const p = {
    id: uuid(), owner, documentId, version: 1, baseDocumentVersion: item.version, baseVersionId: latest(item).id,
    title, content, origin: "codex", status: "pending", acceptedVersionId: null, createdAt: t, updatedAt: t,
  };
  proposals.set(p.id, p);
  return p;
}

function handleWriting(owner, method, path, b) {
  let m;
  if (method === "POST" && (m = /^\/research\/document-proposals\/([^/]+)\/(accept|discard)$/.exec(path))) {
    const p = proposals.get(m[1]);
    if (!p || p.owner !== owner) return fail(404, "not_found", "Not found");
    if (p.status !== "pending" || b.expectedProposalVersion !== p.version) return conflict("proposal_closed");
    if (m[2] === "discard") {
      Object.assign(p, { status: "discarded", version: p.version + 1, updatedAt: now() });
      return ok(200, anyProposalView(p));
    }
    if (p.documentId !== null) return fail(422, "validation_error", "Use the existing document proposal acceptance route");
    const fields = validateItem("document", { title: b.title, content: b.content });
    if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
    const t = now();
    const item = { id: uuid(), type: "document", owner, versions: [newVersion("document", { title: b.title, content: b.content }, 1)], version: 1, archived: false, createdAt: t, updatedAt: t, publication: null };
    items.set(item.id, item);
    Object.assign(p, { documentId: item.id, baseDocumentVersion: 1, baseVersionId: latest(item).id, acceptedVersionId: latest(item).id, status: "accepted", version: p.version + 1, updatedAt: t });
    return ok(200, { proposal: anyProposalView(p), document: detail(item), draft: null });
  }
  if (method === "POST" && path === "/research/document-drafts") {
    const fields = draftFields(b);
    if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
    let base = { documentId: null, baseDocumentVersion: null, baseVersionId: null };
    if (b.documentId !== undefined && b.documentId !== null) {
      const item = owned(b.documentId, owner, "document");
      if (!item) return fail(404, "not_found", "Not found");
      if (item.archived) return conflict("archived");
      if (activeDraftOf(owner, item.id)) return conflict("active_draft_exists");
      if (b.baseDocumentVersion !== item.version) return conflict("document_version");
      if (b.baseVersionId !== latest(item).id) return conflict("base_changed");
      base = { documentId: item.id, baseDocumentVersion: item.version, baseVersionId: latest(item).id };
    }
    const d = { id: uuid(), owner, ...base, revision: 1, title: b.title, content: b.content, updatedAt: now(), state: "active" };
    drafts.set(d.id, d);
    return ok(201, draftView(d));
  }
  if ((m = /^\/research\/document-drafts\/([^/]+)(\/commit)?$/.exec(path))) {
    const d = drafts.get(m[1]);
    if (!d || d.owner !== owner) return fail(404, "not_found", "Not found");
    if (d.state !== "active") return conflict("draft_closed");
    if (b.expectedDraftRevision !== d.revision) return conflict("draft_revision");
    const doc = d.documentId ? owned(d.documentId, owner, "document") : null;
    if (method === "DELETE" && !m[2]) {
      d.state = "discarded";
      d.revision++;
      d.updatedAt = now();
      return ok(200, draftView(d));
    }
    if (method === "PUT" && !m[2]) {
      const fields = draftFields(b);
      if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
      if (doc?.archived) return conflict("archived");
      d.title = b.title;
      d.content = b.content;
      d.revision++;
      d.updatedAt = now();
      return ok(200, draftView(d));
    }
    if (method === "POST" && m[2]) {
      const fields = validateItem("document", { title: d.title, content: d.content });
      if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
      let item;
      if (doc) {
        if (doc.archived) return conflict("archived");
        if (b.expectedVersion !== doc.version) return conflict("document_version");
        const moved = doc.version !== d.baseDocumentVersion || latest(doc).id !== d.baseVersionId;
        if (moved ? b.resolvedBaseVersionId !== latest(doc).id : b.resolvedBaseVersionId !== undefined && b.resolvedBaseVersionId !== latest(doc).id)
          return conflict("base_changed");
        changeDocument(doc, d.title, d.content);
        item = doc;
      } else {
        if (b.expectedVersion !== undefined || b.resolvedBaseVersionId !== undefined) return fail(422, "validation_error", "New drafts have no document base");
        const t = now();
        item = { id: uuid(), type: "document", owner, versions: [newVersion("document", d, 1)], version: 1, archived: false, createdAt: t, updatedAt: t, publication: null };
        items.set(item.id, item);
        Object.assign(d, { documentId: item.id, baseDocumentVersion: item.version, baseVersionId: latest(item).id });
      }
      d.state = "committed";
      d.revision++;
      d.updatedAt = now();
      return ok(200, { draft: draftView(d), document: detail(item) });
    }
  }
  if (method === "POST" && (m = /^\/research\/documents\/([^/]+)\/proposals\/([^/]+)\/(accept|discard)$/.exec(path))) {
    const doc = owned(m[1], owner, "document");
    const p = proposals.get(m[2]);
    if (!doc || !p || p.owner !== owner || p.documentId !== doc.id) return fail(404, "not_found", "Not found");
    if (p.status !== "pending" || b.expectedProposalVersion !== p.version) return conflict("proposal_closed");
    let resolved = null;
    if (m[3] === "accept") {
      if (doc.archived) return conflict("archived");
      if (b.expectedVersion !== doc.version) return conflict("document_version");
      if (b.resolvedBaseVersionId !== latest(doc).id) return conflict("base_changed");
      const fields = validateItem("document", { title: b.title, content: b.content });
      if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
      const active = activeDraftOf(owner, doc.id);
      if (active) {
        if (b.draftId !== active.id) return conflict("draft_revision");
        if (b.expectedDraftRevision !== active.revision) return conflict("draft_revision");
      } else if (b.draftId !== undefined) return conflict("draft_closed");
      changeDocument(doc, b.title, b.content);
      if (active) {
        active.state = "committed";
        active.revision++;
        active.updatedAt = now();
        resolved = draftView(active);
      }
      p.status = "accepted";
      p.acceptedVersionId = latest(doc).id;
    } else p.status = "discarded";
    p.version++;
    p.updatedAt = now();
    return m[3] === "accept" ? ok(200, { proposal: proposalView(p), document: detail(doc), draft: resolved }) : ok(200, proposalView(p));
  }
  return null;
}

function handleMutation(owner, method, path, b, user) {
  if (path === "/research/codex/runner" && method === "POST") return prepareRunner(owner, b);
  const writing = handleWriting(owner, method, path, b);
  if (writing) return writing;
  const post = handlePostMutation(owner, method, path, b, user, postDocs) ?? (method === "DELETE" ? handleImageDelete(owner, path) : null);
  if (post) return post;
  let m;
  // 자료·문서 생성
  if (method === "POST" && (m = /^\/research\/(materials|documents)$/.exec(path))) {
    const type = m[1] === "materials" ? "material" : "document";
    const fields = validateItem(type, b);
    if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
    const t = now();
    const item = { id: uuid(), type, owner, versions: [newVersion(type, b, 1)], version: 1, archived: false, createdAt: t, updatedAt: t, publication: null };
    items.set(item.id, item);
    return ok(201, detail(item));
  }
  if ((m = /^\/research\/(materials|documents)\/([^/]+)$/.exec(path))) {
    const type = m[1] === "materials" ? "material" : "document";
    const item = owned(m[2], owner, type);
    if (!item) return fail(404, "not_found", "Not found");
    if (item.archived) return fail(409, "conflict", "Archived");
    if (stale(b, item.version)) return fail(409, "conflict", "Version conflict");
    if (method === "PATCH") {
      const fields = validateItem(type, b);
      if (fields.length) return fail(422, "validation_error", "Invalid request", { fields });
      item.versions.push(newVersion(type, b, item.versions.length + 1));
      item.version++;
      item.updatedAt = now();
      return ok(200, detail(item));
    }
    if (method === "DELETE") {
      item.archived = true;
      item.version++;
      item.updatedAt = now();
      return ok(200, { id: item.id, archived: true, version: item.version });
    }
  }
  if (method === "POST" && path === "/research/relations") {
    const s = b.source && owned(b.source.id, owner, b.source.type);
    const t = b.target && owned(b.target.id, owner, b.target.type);
    if (!s || !t || s.archived || t.archived) return fail(404, "not_found", "Not found");
    if (s.id === t.id) return fail(422, "validation_error", "Self link", { fields: [{ path: "body.target", message: "self" }] });
    if (s.type !== t.type && !(s.type === "document" && t.type === "material" && b.directed === true))
      return fail(422, "validation_error", "Invalid pair", { fields: [{ path: "body.directed", message: "document->material only" }] });
    if (typeof b.kind !== "string" || !b.kind.trim() || b.kind.length > 80) return fail(422, "validation_error", "Invalid kind", { fields: [{ path: "body.kind", message: "required" }] });
    const dup = [...relations.values()].find(
      (r) =>
        !r.archived &&
        r.owner === owner &&
        r.kind === b.kind &&
        r.directed === b.directed &&
        ((r.source.id === s.id && r.target.id === t.id) || (!b.directed && r.source.id === t.id && r.target.id === s.id))
    );
    if (dup) return fail(409, "duplicate", "Duplicate relation");
    const at = now();
    const rel = {
      id: uuid(),
      owner,
      source: { type: s.type, id: s.id },
      target: { type: t.type, id: t.id },
      kind: b.kind,
      description: b.description ?? "",
      directed: !!b.directed,
      version: 1,
      archived: false,
      createdAt: at,
      updatedAt: at,
    };
    relations.set(rel.id, rel);
    return ok(201, relationView(rel));
  }
  if ((m = /^\/research\/relations\/([^/]+)$/.exec(path))) {
    const rel = relations.get(m[1]);
    if (!rel || rel.owner !== owner || rel.archived) return fail(404, "not_found", "Not found");
    if (stale(b, rel.version)) return fail(409, "conflict", "Version conflict");
    rel.version++;
    rel.updatedAt = now();
    if (method === "PATCH") {
      rel.kind = b.kind;
      rel.description = b.description ?? "";
      return ok(200, relationView(rel));
    }
    rel.archived = true;
    return ok(200, { id: rel.id, archived: true, version: rel.version });
  }
  if ((m = /^\/research\/documents\/([^/]+)\/publications?$/.exec(path))) {
    const doc = owned(m[1], owner, "document");
    if (!doc) return fail(404, "not_found", "Not found");
    if (researchMode.publish !== "allowed") return fail(503, "policy_pending", "Policy pending");
    if (stale(b, doc.version)) return fail(409, "conflict", "Version conflict");
    if (method === "POST" && doc.archived) return fail(409, "conflict", "Archived document cannot be published");
    if (method === "DELETE") {
      retiredReleases.add(doc.publication?.id);
      doc.publication = null;
      doc.version++;
      return ok(200, { id: doc.id, revoked: true, version: doc.version });
    }
    const v = doc.versions.find((x) => x.id === b.versionId);
    if (!v) return fail(404, "not_found", "Not found");
    const pv = previewOf(doc, v);
    if (b.previewToken !== pv.previewToken) return fail(409, "conflict", "Preview is stale");
    if (!pv.publishable) return fail(422, "validation_error", "Archived reference", { fields: [{ path: "body.previewToken", message: "archived reference" }] });
    const mats = pv.materials.map((m) => ({
      id: m.id,
      versionId: m.versionId,
      snapshot: { id: m.id, versionId: m.versionId, number: m.number, title: m.title, sourceUrl: m.sourceUrl, collectedAt: m.collectedAt, contentKind: m.contentKind, content: m.content },
    }));
    // 이전 공개본은 기록만 남기고 더 이상 열람되지 않는다(최신만)
    if (doc.publication) retiredReleases.add(doc.publication.id);
    doc.publication = { id: uuid(), versionId: v.id, publishedAt: now(), materials: mats, authorLogin: user.login };
    doc.version++;
    return ok(201, snapshot(doc));
  }
  if (method === "POST" && path === "/research/sessions") {
    if (typeof b.title !== "string" || !b.title.trim()) return fail(422, "validation_error", "Invalid", { fields: [{ path: "body.title", message: "required" }] });
    const at = now();
    const s = { id: uuid(), owner, title: b.title, state: "idle", createdAt: at, updatedAt: at, version: 1, items: [], error: null, archived: false };
    if (b.publicDocumentId) {
      const doc = items.get(b.publicDocumentId);
      if (!doc || !doc.publication) return fail(404, "not_found", "Not found");
      // 당시 공개본과 그 참고자료를 맥락으로 저장
      s.context = { documentId: doc.id, publicationId: doc.publication.id, title: snapshot(doc).title };
    }
    sessions.set(s.id, s);
    return ok(201, sessionSummary(s));
  }
  if ((m = /^\/research\/sessions\/([^/]+)(\/messages)?$/.exec(path))) {
    const s = sessions.get(m[1]);
    if (!s || s.owner !== owner) return fail(404, "not_found", "Not found");
    if (stale(b, s.version) || s.state === "running") return fail(409, "conflict", "Conflict");
    if (method === "DELETE" && !m[2]) {
      s.archived = true;
      s.version++;
      return ok(200, { id: s.id, archived: true, version: s.version });
    }
    if (method === "POST" && m[2]) {
      if (Object.keys(b).some((k) => !["text", "expectedVersion", "page"].includes(k))) return fail(422, "validation_error", "Unknown field");
      const resolved = resolvePage(owner, b.page);
      if (resolved.error) return resolved.error;
      const scope = resolved.scope;
      // #10: 실행 환경이 사용 가능하지 않으면 접수 전 확정 거절(예약·기록 없음)
      if (runnerMode.initial && runnerOf(owner) !== "ready") {
        const st = runnerOf(owner);
        const code = Object.hasOwn(RUNNER_PREFLIGHT, st) ? RUNNER_PREFLIGHT[st] : st === "auth_expired" || st === "auth_revoked" ? `codex_${st}` : "codex_unavailable";
        return fail(503, code, "Runner is not ready");
      }
      if (GATED_REASONS.includes(researchMode.codex)) return fail(503, `codex_${researchMode.codex}`, "Codex is not available");
      // 계약에 없는 codex_* 503: 결과 불명으로 취급되어야 한다(서버는 아무것도 하지 않음)
      if (researchMode.codex === "unknown_code") return fail(503, "codex_upstream_timeout", "Upstream timeout");
      if (researchMode.codex === "rate_limited") return fail(503, "codex_rate_limited", "Provider usage limit");
      if (MIDTURN[researchMode.codex]) {
        const mt = MIDTURN[researchMode.codex];
        const item = mt.recorded
          ? { id: uuid(), type: "message", role: "user", text: b.text, raw: { type: "userMessage", text: b.text }, ...scope }
          : { id: uuid(), type: "message", role: "user", text: b.text, source: "platform", status: "pending", raw: { type: "platformInput", source: "platform", text: b.text, status: "pending" }, ...scope };
        s.scope = scope;
        s.items.push(item);
        s.error = null;
        s.state = "running";
        s.version++;
        s.updatedAt = now();
        setTimeout(() => {
          if (!mt.recorded) {
            item.status = "not_recorded";
            item.raw.status = "not_recorded";
          }
          s.state = "failed";
          s.error = { code: mt.code, message: "upstream 401 token_expired acct_secret" };
          s.version++;
          s.updatedAt = now();
          researchMode.codex = mt.then;
        }, 1500);
        return ok(202, sessionSummary(s));
      }
      if (researchMode.codex === "mismatch") {
        // 계약: 응답 모델 불일치/우회 → 해당 turn의 assistant 결과는 projection에서 제외, 남는 user/tool 항목에 modelMismatch,
        // polling codex_model_unavailable, 계정 runner는 운영자가 해제할 때까지 차단(durable)
        const user = { id: uuid(), type: "message", role: "user", text: b.text, modelMismatch: true, raw: { type: "userMessage", text: b.text }, ...scope };
        s.scope = scope;
        s.items.push(user);
        s.state = "running";
        s.version++;
        s.updatedAt = now();
        setTimeout(() => {
          s.items.push({
            id: uuid(),
            type: "tool_call",
            name: "research_document_save",
            input: { title: "blocked" },
            output: { error: "blocked" },
            status: "failed",
            modelMismatch: true,
            raw: { type: "dynamicToolCall", blocked: true },
          });
          s.state = "failed";
          s.error = { code: "codex_model_unavailable", message: "Provider returned model gpt-other" };
          s.version++;
          s.updatedAt = now();
          researchMode.codex = "model_unavailable";
        }, 1500);
        return ok(202, sessionSummary(s));
      }
      if (researchMode.codex === "reject") {
        const item = { id: uuid(), type: "message", role: "user", text: b.text, source: "platform", status: "pending", ...scope };
        s.scope = scope;
        item.raw = { type: "platformInput", source: "platform", text: b.text, status: "pending" };
        s.items.push(item);
        s.state = "running";
        s.version++;
        s.updatedAt = now();
        setTimeout(() => {
          item.status = "not_recorded";
          item.raw.status = "not_recorded";
          s.state = "failed";
          s.error = { code: "codex_rejected", message: "Codex rejected the turn" };
          s.version++;
          s.updatedAt = now();
        }, 1500);
        return ok(202, sessionSummary(s));
      }
      if (researchMode.codex !== "fixture") return fail(503, "codex_unavailable", "Codex unavailable");
      const closed = windowReason();
      if (closed) return fail(503, `codex_${closed}`, "Codex is not available");
      if (researchMode.remaining !== null) researchMode.remaining--;
      s.error = null;
      s.items.push({ id: uuid(), type: "message", role: "user", text: b.text, raw: { kind: "user_message", text: b.text }, ...scope });
      s.scope = scope;
      s.state = "running";
      s.version++;
      s.updatedAt = now();
      setTimeout(() => {
        // 모의 모델은 항상 research_list를 호출한다. 이 요청 범위에 없으면 서버 callback 거부(tool_not_allowed)로 투영한다.
        const input = { type: "material", limit: 5, query: b.text };
        if (scope.allowedTools.includes("research_list")) {
          const output = { items: [], note: "fixture" };
          s.items.push({ id: uuid(), type: "tool_call", name: "research_list", input, output, status: "completed", raw: { kind: "function_call", arguments: input, result: output, fixture: true } });
        } else {
          const output = { error: { code: "tool_not_allowed" } };
          s.items.push({ id: uuid(), type: "tool_call", name: "research_list", input, output, status: "rejected", error: { code: "tool_not_allowed" }, raw: { kind: "function_call", arguments: input, result: output, fixture: true } });
        }
        // #23 api-contracts/issue23-shell-display.md: /fixture/shell-{ok,fail,timeout,busy,cancel,long}이면 shell 실행 결과를 투영한다.
        const shellCase = /\/fixture\/shell-([a-z]+)/.exec(b.text)?.[1];
        if (shellCase) s.items.push(shellItem(shellCase, scope));
        // #22 /fixture/propose: 보고 있는 문서에 Codex 제안을 만든다(research_document_update는 제안만 만들고 문서를 바꾸지 않는다).
        if (b.text.includes("/fixture/propose") && scope.page.kind === "document" && scope.capability === "owner_research") {
          const doc = owned(scope.page.resourceId, owner, "document");
          const input = { id: scope.page.resourceId, mutationId: uuid(), expectedVersion: doc?.version, title: doc ? `${latest(doc).title} (제안)` : "", content: doc ? `${latest(doc).content}\nCodex가 덧붙인 문장` : "" };
          const p = doc ? createProposal(owner, doc.id, input.title, input.content) : null;
          const output = p ? [{ type: "inputText", text: JSON.stringify(proposalView(p, false)) }] : { error: { code: "not_found" } };
          s.items.push({ id: uuid(), type: "tool_call", name: "research_document_update", input, output, status: p ? "completed" : "failed", raw: { kind: "function_call", arguments: input, result: output, fixture: true } });
        }
        // #28 /fixture/post-propose: 보고 있는 글(또는 작성 중인 새 글 초안, 없으면 새 글)에 Codex 글 제안을 만든다.
        if (b.text.includes("/fixture/post-propose") && scope.capability === "owner_research" && (scope.page.kind === "post" || scope.page.kind === "post_workspace")) {
          const target = scope.page.kind === "post" ? { postId: scope.page.resourceId } : scope.page.draftId ? { draftId: scope.page.draftId } : {};
          const p = createPostProposal(owner, target, (from) => ({
            title: from.title ? `${from.title} (제안)` : "Codex가 쓴 새 글",
            content: `${from.content ? `${from.content}\n` : ""}Codex가 덧붙인 문단`,
            description: from.description || "Codex 요약",
            tags: [...from.tags, "codex"].filter((t, i, a) => a.indexOf(t) === i),
            slug: from.slug,
          }));
          const input = { mutationId: uuid(), ...target };
          const output = p ? [{ type: "inputText", text: JSON.stringify(p) }] : { error: { code: "not_found" } };
          s.items.push({ id: uuid(), type: "tool_call", name: "research_post_propose", input, output, status: p ? "completed" : "failed", raw: { kind: "function_call", arguments: input, result: output, fixture: true } });
        }
        // #28 D1 /fixture/doc-save: research_document_save는 새 문서를 바로 만들지 않고 제안을 남긴다.
        if (b.text.includes("/fixture/doc-save") && scope.capability === "owner_research") {
          const t = now();
          const p = {
            id: uuid(), owner, documentId: null, version: 1, baseDocumentVersion: null, baseVersionId: null,
            title: "Codex 새 문서", content: "Codex가 정리한 새 문서 본문", origin: "codex", status: "pending", acceptedVersionId: null, createdAt: t, updatedAt: t,
          };
          proposals.set(p.id, p);
          const input = { mutationId: uuid(), title: p.title, content: p.content };
          const output = [{ type: "inputText", text: JSON.stringify(anyProposalView(p, false)) }];
          s.items.push({ id: uuid(), type: "tool_call", name: "research_document_save", input, output, status: "completed", raw: { kind: "function_call", arguments: input, result: output, fixture: true } });
        }
        const answer = b.text.includes("/fixture/wide-answer") ? WIDE_ANSWER : `모의 응답: **${b.text}**`;
        s.items.push({ id: uuid(), type: "message", role: "assistant", text: answer, raw: { kind: "agent_message", fixture: true } });
        s.state = "idle";
        s.version++;
        s.updatedAt = now();
      }, researchMode.turnMs);
      return ok(202, sessionSummary(s));
    }
  }
  return fail(404, "not_found", "Not found");
}

// 실행기 JSON 결과 모양(backend/executor/server.py). 공개 문서 모드는 shell 불가(tool_not_allowed).
function shellItem(kind, scope) {
  const command = kind === "long" ? `echo ${"x".repeat(200)}\nsecond line` : `fixture-${kind} --run`;
  const input = { command, cwd: "notes" };
  const base = { requestId: uuid(), stdout: "", stderr: "", truncated: false, durationMs: 12, wallSeconds: 30 };
  let status, output, error;
  if (scope.capability !== "owner_research") {
    status = "rejected";
    output = { status, error: { code: "tool_not_allowed" } };
    error = { code: "tool_not_allowed" };
  } else if (kind === "busy") {
    status = "rejected";
    output = { status, error: { code: "shell_busy" } };
    error = { code: "shell_busy" };
  } else if (kind === "fail") {
    status = "failed";
    output = { ...base, status, exitCode: 2, stderr: "ls: cannot access 'missing': No such file or directory\n" };
  } else if (kind === "timeout") {
    status = "timed_out";
    output = { ...base, status, exitCode: null, stdout: "partial\n", truncated: true, durationMs: 30004, error: { code: "shell_timeout" } };
  } else if (kind === "cancel") {
    status = "failed";
    output = { ...base, status, exitCode: null, error: { code: "shell_cancelled" } };
  } else {
    status = "completed";
    output = { ...base, status, exitCode: 0, stdout: "shell fixture output\n" };
  }
  const item = { id: uuid(), type: "tool_call", name: "shell", input, output, status, raw: { kind: "function_call", arguments: input, result: output, fixture: true } };
  if (error) item.error = error;
  return item;
}

function sessionSummary(s) {
  const out = { id: s.id, title: s.title, state: s.state, createdAt: s.createdAt, updatedAt: s.updatedAt, version: s.version };
  if (s.context) out.context = s.context;
  // #9: 가장 최근 접수 요청의 범위(없으면 general/owner_research)
  Object.assign(out, s.scope ?? GENERAL_SCOPE);
  return out;
}

// #9 api-contracts/issue9-page-chat.md 페이지 범위. 서버가 자원을 확인해 정한다(브라우저의 도구 목록·URL을 받지 않는다).
// #14: 소유자 화면은 모두 같은 owner_research(소유자 DB 도구 전부)이고 page는 힌트다. 공개 글만 읽기 범위로 남는다.
const OWNER_TOOLS = [
  "research_list", "research_get", "research_versions", "research_version_get", "research_relations",
  "research_material_save", "research_document_save", "research_material_update", "research_document_update",
  "research_relation_save", "research_relation_update", "research_archive",
  // #28
  "research_post_propose", "research_post_draft_get",
];
const OWNER = { capability: "owner_research", tools: OWNER_TOOLS };
const PAGE_KINDS = {
  general: OWNER,
  public_document: { capability: "public_context_read", tools: ["research_page_context"], detail: true },
  material: { ...OWNER, detail: true },
  document: { ...OWNER, detail: true },
  material_workspace: OWNER,
  document_workspace: OWNER,
  // #28: 새 글 초안은 draftId(별도 필드), 기존 글은 resourceId=postId, 공개 글은 resourceId=PublicPost.postId
  post_workspace: { ...OWNER, draft: true },
  post: { ...OWNER, detail: true },
  public_post: { capability: "public_post_read", tools: ["research_page_context"], detail: true },
};
const GENERAL_SCOPE = { page: { kind: "general" }, capability: OWNER.capability, allowedTools: OWNER.tools };

function resolvePage(owner, p) {
  if (p === undefined) return { scope: GENERAL_SCOPE };
  const bad = (message) => ({ error: fail(422, "validation_error", "Invalid request", { fields: [{ path: "body.page", message }] }) });
  if (!p || typeof p !== "object" || Array.isArray(p)) return bad("object");
  if (Object.keys(p).some((k) => k !== "kind" && k !== "resourceId" && k !== "draftId")) return bad("unknown field");
  const def = PAGE_KINDS[p.kind];
  if (!def) return bad("kind");
  if (p.draftId !== undefined && !def.draft) return bad("draftId not allowed");
  if (!def.detail) {
    if (p.resourceId !== undefined) return bad("resourceId not allowed");
    if (p.draftId !== undefined) {
      if (typeof p.draftId !== "string" || !UUID_RE.test(p.draftId)) return bad("draftId");
      const d = newPostDraftOf(owner, p.draftId);
      if (!d) return { error: fail(404, "not_found", "Not found") };
      return { scope: { page: { kind: p.kind, draftId: p.draftId, title: d.title }, capability: def.capability, allowedTools: def.tools } };
    }
    return { scope: { page: { kind: p.kind }, capability: def.capability, allowedTools: def.tools } };
  }
  if (typeof p.resourceId !== "string" || !UUID_RE.test(p.resourceId)) return bad("resourceId");
  if (p.kind === "post" || p.kind === "public_post") {
    const t = p.kind === "post" ? postTitle(owner, p.resourceId) : publicPostTitle(p.resourceId);
    if (t === null) return { error: fail(404, "not_found", "Not found") };
    return { scope: { page: { kind: p.kind, resourceId: p.resourceId, title: t }, capability: def.capability, allowedTools: def.tools } };
  }
  const item = items.get(p.resourceId);
  let title;
  if (p.kind === "public_document") {
    if (!item || item.type !== "document" || !item.publication) return { error: fail(404, "not_found", "Not found") };
    title = snapshot(item).title;
  } else {
    if (!item || item.owner !== owner || item.type !== p.kind || item.archived) return { error: fail(404, "not_found", "Not found") };
    title = latest(item).title;
  }
  return { scope: { page: { kind: p.kind, resourceId: p.resourceId, title }, capability: def.capability, allowedTools: def.tools } };
}

const retiredReleases = new Set();

// 공개 미리보기: 글→자료 직접 관계 전체(보관되지 않은 관계), 자료의 현재 버전. 간접 연결은 따라가지 않는다.
function previewOf(doc, v) {
  const byMaterial = new Map();
  for (const r of relations.values()) {
    if (r.archived || r.source.id !== doc.id || r.target.type !== "material") continue;
    const entry = byMaterial.get(r.target.id) ?? [];
    entry.push({ id: r.id, version: r.version });
    byMaterial.set(r.target.id, entry);
  }
  const materials = [...byMaterial.entries()].map(([id, rels]) => {
    const mat = items.get(id);
    const mv = latest(mat);
    return {
      id,
      versionId: mv.id,
      number: mv.number,
      title: mv.title,
      sourceUrl: mv.sourceUrl,
      collectedAt: mv.collectedAt,
      contentKind: mv.contentKind,
      content: mv.content,
      archived: mat.archived,
      relations: rels,
    };
  });
  const token = crypto
    .createHash("sha256")
    .update(JSON.stringify([doc.version, v.id, materials.map((m) => [m.id, m.versionId, m.archived, m.relations])]))
    .digest("hex");
  // 계약: 서버가 발급한 64자리 소문자 hex 문자열
  const previewToken = token;
  return {
    documentId: doc.id,
    versionId: v.id,
    title: v.title,
    content: v.content,
    expectedVersion: doc.version,
    previewToken,
    publishable: materials.every((m) => !m.archived),
    materials,
  };
}

function handleRead(owner, path, url) {
  let m;
  const post = handlePostRead(owner, path, url, postDocs) ?? handleImageRead(owner, path, url);
  if (post) return post;
  if (path === "/research/document-proposals") {
    const list = [...proposals.values()].filter((p) => p.owner === owner).sort((x, y) => (x.createdAt < y.createdAt ? 1 : -1)).map((p) => anyProposalView(p, false));
    return ok(200, page(list, url));
  }
  if ((m = /^\/research\/document-proposals\/([^/]+)$/.exec(path))) {
    const p = proposals.get(m[1]);
    return p && p.owner === owner ? ok(200, anyProposalView(p)) : fail(404, "not_found", "Not found");
  }
  if (path === "/research/document-drafts") {
    const list = [...drafts.values()].filter((d) => d.owner === owner && d.state === "active").sort(byUpdated).map((d) => draftView(d, false));
    return ok(200, page(list, url));
  }
  if ((m = /^\/research\/document-drafts\/([^/]+)$/.exec(path))) {
    const d = drafts.get(m[1]);
    return d && d.owner === owner ? ok(200, draftView(d)) : fail(404, "not_found", "Not found");
  }
  if ((m = /^\/research\/documents\/([^/]+)\/(draft|proposals)(?:\/([^/]+))?$/.exec(path))) {
    const doc = owned(m[1], owner, "document");
    if (!doc) return fail(404, "not_found", "Not found");
    if (m[2] === "draft" && !m[3]) {
      const d = activeDraftOf(owner, doc.id);
      return ok(200, { draft: d ? draftView(d) : null });
    }
    if (m[2] === "proposals" && !m[3]) {
      const list = [...proposals.values()].filter((p) => p.owner === owner && p.documentId === doc.id).sort((x, y) => (x.createdAt < y.createdAt ? 1 : -1));
      return ok(200, page(list.map((p) => proposalView(p, false)), url));
    }
    if (m[2] === "proposals") {
      const p = proposals.get(m[3]);
      return p && p.owner === owner && p.documentId === doc.id ? ok(200, proposalView(p)) : fail(404, "not_found", "Not found");
    }
  }
  if ((m = /^\/research\/(materials|documents)$/.exec(path))) {
    const type = m[1] === "materials" ? "material" : "document";
    const archived = url.searchParams.get("archived") === "true";
    const list = [...items.values()].filter((i) => i.owner === owner && i.type === type && i.archived === archived).sort(byUpdated).map(summary);
    return ok(200, page(list, url));
  }
  if ((m = /^\/research\/(materials|documents)\/([^/]+)(?:\/(versions|relations)(?:\/([^/]+))?)?$/.exec(path))) {
    const item = owned(m[2], owner, m[1] === "materials" ? "material" : "document");
    if (!item) return fail(404, "not_found", "Not found");
    if (!m[3]) return ok(200, detail(item));
    if (m[3] === "versions" && !m[4]) return ok(200, page([...item.versions].reverse().map((v) => versionView(item, v, false)), url));
    if (m[3] === "versions") {
      const v = item.versions.find((x) => x.id === m[4]);
      return v ? ok(200, versionView(item, v, true)) : fail(404, "not_found", "Not found");
    }
    if (m[3] === "relations" && !m[4]) {
      const list = [...relations.values()].filter((r) => r.owner === owner && !r.archived && (r.source.id === item.id || r.target.id === item.id));
      return ok(200, page(list.map((r) => relationView(r, item.id)), url));
    }
  }
  if ((m = /^\/research\/documents\/([^/]+)\/publication-preview$/.exec(path))) {
    const doc = owned(m[1], owner, "document");
    if (!doc) return fail(404, "not_found", "Not found");
    const vid = url.searchParams.get("versionId");
    const v = vid ? doc.versions.find((x) => x.id === vid) : latest(doc);
    return v ? ok(200, previewOf(doc, v)) : fail(404, "not_found", "Not found");
  }
  if (path === "/research/codex/status") {
    // #10 service mode: 계정별 실행 환경 상태(조회는 상태를 바꾸지 않는다). 시험 창 필드는 보내지 않는다.
    if (runnerMode.initial) return ok(200, runnerStatus(owner));
    if (GATED_REASONS.includes(researchMode.codex)) {
      // #7 계약: 계정별 선접수 불가 사유 + 서버 고정 모델(인증 정보·계정 식별자 없음)
      return ok(200, { available: false, reason: researchMode.codex, verification: "unverified", model: FIXED_MODEL });
    }
    // 접수 가능한 연결에서 일어나는 실패를 흉내 내는 모드: 상태는 접수 가능(fixture)
    if (["mismatch", "unknown_code", "reject"].includes(researchMode.codex) || MIDTURN[researchMode.codex]) {
      return ok(200, { available: true, reason: null, verification: "fixture", model: FIXED_MODEL });
    }
    if (researchMode.codex === "rate_limited") {
      return ok(200, { available: false, reason: "provider_rate_limited", verification: "unverified", model: FIXED_MODEL });
    }
    if (researchMode.codex === "fixture" && (researchMode.windowEnd !== null || researchMode.remaining !== null)) {
      const closed = windowReason();
      return ok(200, { available: !closed, reason: closed, verification: "fixture", model: FIXED_MODEL, ...windowFields() });
    }
    return ok(200, researchMode.codex === "fixture" ? { available: true, reason: null, verification: "fixture" } : { available: false, reason: "not_configured", verification: "unverified" });
  }
  if (path === "/research/sessions") {
    const list = [...sessions.values()].filter((s) => s.owner === owner && !s.archived).sort(byUpdated).map(sessionSummary);
    return ok(200, page(list, url));
  }
  if ((m = /^\/research\/sessions\/([^/]+)(?:\/items\/([^/]+))?$/.exec(path))) {
    const s = sessions.get(m[1]);
    if (!s || s.owner !== owner) return fail(404, "not_found", "Not found");
    if (m[2]) {
      const it = s.items.find((x) => x.id === m[2]);
      return it ? ok(200, { id: it.id, format: "json", raw: it.raw }) : fail(404, "not_found", "Not found");
    }
    // 표시용 projection에는 원본(raw)을 싣지 않는다. 원본은 item GET으로만 제공한다.
    const projection = s.items.map((it) => Object.fromEntries(Object.entries(it).filter(([k]) => k !== "raw")));
    return ok(200, { ...sessionSummary(s), items: projection, error: s.error });
  }
  return fail(404, "not_found", "Not found");
}

/** `/research/...`, `/public/...` 요청 처리. user는 현재 session 사용자(없으면 null). */
export async function handleResearch(req, res, path, url, session) {
  // #28 이미지 원본: 발행본에 쓰인 것은 누구나, 아니면 소유자만(DB 권한 검사 흉내). 없거나 권한이 없으면 404.
  const media = /^\/media\/post-images\/([^/]+)$/.exec(path);
  if (media) {
    if (req.method !== "GET") return send(res, 405, { error: { code: "method_not_allowed", message: "Method not allowed" } });
    const found = mediaBytes(session?.user.accountId ?? null, media[1]);
    if (!found) return send(res, 404, { error: { code: "not_found", message: "Not found" } });
    res.writeHead(200, {
      "Content-Type": found.type,
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "default-src 'none'",
      "Cache-Control": found.isPublic ? "public, max-age=0, must-revalidate" : "no-store",
    });
    return res.end(found.bytes);
  }
  if (path.startsWith("/public/")) {
    if (req.method !== "GET") return send(res, 405, { error: { code: "method_not_allowed", message: "Method not allowed" } });
    const post = handlePublicPosts(path, url);
    if (post) return send(res, post.status, post.body);
    // 실제 계약: 공개는 문서 보관과 독립적이다(보관해도 철회 전까지 공개 유지).
    const published = [...items.values()].filter((i) => i.type === "document" && i.publication);
    if (path === "/public/documents") {
      const list = published.map((d) => {
        const s = snapshot(d);
        return { id: s.id, documentId: d.id, title: s.title, publishedAt: s.publishedAt, author: s.author };
      });
      return send(res, 200, page(list, url));
    }
    const rel = /^\/public\/releases\/([^/]+)$/.exec(path);
    if (rel) {
      const d = published.find((x) => x.publication.id === rel[1] && !retiredReleases.has(rel[1]));
      return d ? send(res, 200, snapshot(d)) : send(res, 404, { error: { code: "not_found", message: "Not found" } });
    }
    const m = /^\/public\/documents\/([^/]+)$/.exec(path);
    const doc = m && published.find((d) => d.id === m[1]);
    return doc ? send(res, 200, snapshot(doc)) : send(res, 404, { error: { code: "not_found", message: "Not found" } });
  }

  if (path === "/research/health") {
    if (req.method !== "GET") return send(res, 405, { error: { code: "method_not_allowed", message: "Method not allowed" } });
    return send(res, 200, { status: "ok" });
  }
  if (!session) return send(res, 401, { error: { code: "unauthenticated", message: "Authentication required" } });
  if (researchMode.access !== "approved") {
    return send(res, 503, { error: { code: "policy_pending", message: "Policy pending" } });
  }
  // 확정 정책: 승인된 editor/admin만
  if (!session.user.isApproved || !["editor", "admin"].includes(session.user.role)) {
    return send(res, 403, { error: { code: "forbidden", message: "Research access is not permitted" } });
  }
  const owner = session.user.accountId;
  if (req.method === "GET") {
    const r = handleRead(owner, path, url);
    return send(res, r.status, r.body);
  }

  if (req.headers.origin !== `http://${req.headers.host}`) return send(res, 403, { error: { code: "origin_not_allowed", message: "Origin not allowed" } });
  if (researchMode.csrf === "reject" || req.headers["x-csrf-token"] !== session.csrf) return send(res, 403, { error: { code: "csrf_invalid", message: "Invalid CSRF token" } });
  const key = req.headers["idempotency-key"];
  if (typeof key !== "string" || !UUID_RE.test(key)) {
    return send(res, 422, { error: { code: "validation_error", message: "Idempotency-Key required", fields: [{ path: "header.Idempotency-Key", message: "uuid" }] } });
  }
  if (req.method === "POST" && path === "/research/post-images") {
    // 원본 바이트(multipart 아님). 상한을 넘으면 더 읽지 않고 413.
    const chunks = [];
    let size = 0;
    for await (const c of req) {
      size += c.length;
      if (size > IMAGE_LIMIT) break;
      chunks.push(c);
    }
    const bytes = Buffer.concat(chunks);
    const type = (req.headers["content-type"] ?? "").split(";")[0].trim().toLowerCase();
    const r = size > IMAGE_LIMIT ? { status: 413, body: { error: { code: "payload_too_large", message: "Image exceeds size limit" } } } : uploadImage(owner, type, bytes);
    const fp = `POST ${path} ${type} ${crypto.createHash("sha256").update(bytes).digest("hex")}`;
    const prev = idem.get(`${owner}:${key}`);
    if (prev) return prev.fp !== fp ? send(res, 409, { error: { code: "idempotency_conflict", message: "Idempotency conflict" } }) : send(res, prev.status, prev.body);
    idem.set(`${owner}:${key}`, { fp, status: r.status, body: r.body });
    return send(res, r.status, r.body);
  }
  const body = await readBody(req);
  if (body === null || typeof body !== "object") return send(res, 422, { error: { code: "validation_error", message: "Invalid JSON" } });
  const fp = `${req.method} ${path} ${JSON.stringify(body)}`;
  const prev = idem.get(`${owner}:${key}`);
  if (prev) {
    if (prev.fp !== fp) return send(res, 409, { error: { code: "idempotency_conflict", message: "Idempotency conflict" } });
    return send(res, prev.status, prev.body);
  }
  const r = handleMutation(owner, req.method, path, body, session.user);
  // 일시적 정책/연결 실패는 재생 대상으로 저장하지 않는다.
  if (r.status !== 503) idem.set(`${owner}:${key}`, { fp, status: r.status, body: r.body });
  return send(res, r.status, r.body);
}

export function researchCounts() {
  return {
    materials: [...items.values()].filter((i) => i.type === "material").length,
    documents: [...items.values()].filter((i) => i.type === "document").length,
    relations: [...relations.values()].filter((r) => !r.archived).length,
    sessions: sessions.size,
  };
}
