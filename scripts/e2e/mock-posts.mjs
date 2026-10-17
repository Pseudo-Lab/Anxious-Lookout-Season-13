// #28 글(post) 모의 API(api-contracts/issue28-posts.md). mock-research.mjs가 문서 조회 함수를 넘겨 호출한다.
// 실제 backend를 대체하지 않으며 front가 쓰는 계약 경로와 409/422 갈래만 흉내 낸다.
import crypto from "node:crypto";

const BASE = process.env.BASE_PATH ?? "";
const now = () => new Date().toISOString();
const uuid = () => crypto.randomUUID();
const fail = (status, code, message, extra = {}) => ({ status, body: { error: { code, message, ...extra } } });
const ok = (status, body) => ({ status, body });
const notFound = () => fail(404, "not_found", "Not found");
const conflict = (reason) => fail(409, "conflict", "The post, draft or proposal changed; compare before retrying", { details: { reason } });

const posts = new Map(); // id -> { id, owner, legacy, version, archived, createdAt, updatedAt, authorLogin, authorName, versions[], publication, slug }
const drafts = new Map(); // id -> draft row(+owner)
const images = new Map(); // id -> { id, owner, ... }
const proposals = new Map(); // id -> post proposal row(+owner)
const reservedSlugs = new Set();

function page(list, url) {
  const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 30)));
  const start = Number(url.searchParams.get("cursor") ?? 0);
  return { items: list.slice(start, start + limit), nextCursor: start + limit < list.length ? String(start + limit) : null };
}

// ---- 검증 ----

const DRAFT_SLUG = /^[a-z0-9-]*$/;
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** 초안·제안 공통 PostText 형식 검사. strict=true면 commit/accept 규칙(본문·제목·slug 필수, 엄격 slug). */
function textFields(b, strict) {
  const f = [];
  const str = (k, max) => {
    if (typeof b[k] !== "string" || b[k].length > max) f.push({ path: `body.${k}`, message: `string<=${max}` });
  };
  str("title", 300);
  str("content", 200000);
  str("description", 1000);
  if (typeof b.slug !== "string" || b.slug.length > 100 || !DRAFT_SLUG.test(b.slug)) f.push({ path: "body.slug", message: "[a-z0-9-]*<=100" });
  else if (strict && !SLUG.test(b.slug)) f.push({ path: "body.slug", message: "slug" });
  if (!Array.isArray(b.tags) || b.tags.length > 20 || b.tags.some((t) => typeof t !== "string" || t.trim().length < 1 || t.trim().length > 50))
    f.push({ path: "body.tags", message: "<=20 tags, 1-50 chars" });
  if (b.coverImageId !== null && typeof b.coverImageId !== "string") f.push({ path: "body.coverImageId", message: "uuid|null" });
  if (!Array.isArray(b.documentIds) || b.documentIds.length > 50 || new Set(b.documentIds).size !== b.documentIds.length || b.documentIds.some((d) => typeof d !== "string"))
    f.push({ path: "body.documentIds", message: "unique uuids<=50" });
  if (strict && f.length === 0) {
    if (!b.title.trim()) f.push({ path: "body.title", message: "required" });
    if (!b.content.trim()) f.push({ path: "body.content", message: "required" });
  }
  return f;
}
const invalid = (fields) => fail(422, "validation_error", "Invalid request", { fields });

function text(b) {
  const tags = [];
  for (const t of b.tags) if (!tags.includes(t.trim())) tags.push(t.trim());
  return { title: b.title, content: b.content, description: b.description, slug: b.slug, tags, coverImageId: b.coverImageId, documentIds: [...b.documentIds] };
}

const OWN_IMAGE = new RegExp(`\\]\\(${BASE.replace(/\./g, "\\.")}/api/media/post-images/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\\)`, "g");
/** 본문의 자체 이미지 ID(정규 형태만, 모의 추출). */
const contentImages = (content) => [...content.matchAll(OWN_IMAGE)].map((m) => m[1]);
const imageIdsOf = (t) => [...new Set([...(t.coverImageId ? [t.coverImageId] : []), ...contentImages(t.content ?? "")])];

/** commit/accept/publish 재검증: 문서는 본인 활성, 표지·본문 이미지는 본인 이미지. */
function refsFields(owner, t, docs) {
  const f = [];
  for (const id of contentImages(t.content ?? "")) if (images.get(id)?.owner !== owner) f.push({ path: "body.content", message: `unknown image: ${id}` });
  for (const id of t.documentIds) {
    const d = docs.get(owner, id);
    if (!d || d.archived) f.push({ path: "body.documentIds", message: `not an active document: ${id}` });
  }
  if (t.coverImageId !== null && images.get(t.coverImageId)?.owner !== owner) f.push({ path: "body.coverImageId", message: "unknown image" });
  return f;
}

// ---- 표현 ----

const latest = (p) => p.versions.at(-1);
function versionView(p, v, withContent = true) {
  const out = { ...v, postId: p.id };
  if (!withContent) delete out.content;
  return out;
}
function postView(p, withContent = true) {
  return {
    id: p.id,
    version: p.version,
    archived: p.archived,
    createdAt: p.createdAt,
    updatedAt: p.updatedAt,
    author: { login: p.authorLogin },
    latestVersion: versionView(p, latest(p), withContent),
    publication: p.publication && { id: p.publication.id, versionId: p.publication.versionId, publishedAt: p.publication.publishedAt, slug: p.slug },
  };
}
function draftView(d, withContent = true) {
  const out = { ...d };
  delete out.owner;
  if (!withContent) delete out.content;
  return out;
}
const imageUrl = (id) => `${BASE}/api/media/post-images/${id}`;
function publicView(p, withContent = true) {
  const pub = p.publication;
  const v = p.versions.find((x) => x.id === pub.versionId);
  const out = {
    id: pub.id,
    postId: p.id,
    versionId: v.id,
    slug: p.slug,
    title: v.title,
    description: v.description,
    content: v.content,
    tags: v.tags,
    coverImageUrl: v.coverImageId ? imageUrl(v.coverImageId) : null,
    author: { login: pub.authorLogin, name: pub.authorName },
    publishedAt: pub.publishedAt,
    documents: pub.documents.map((d) => ({ ...d })),
  };
  if (!withContent) delete out.content;
  return out;
}

// ---- 기존 MDX 3개 이관(0006 seed 흉내: owner 없음, login null) ----

function seed(slug, title, description, date, author, tags, content) {
  const id = uuid();
  const v = { id: uuid(), number: 1, title, content, description, slug, tags, coverImageId: null, documentIds: [], createdAt: `${date}T00:00:00Z` };
  posts.set(id, {
    id, owner: null, legacy: true, version: 1, archived: false, createdAt: v.createdAt, updatedAt: v.createdAt,
    authorLogin: null, authorName: author, versions: [v], slug,
    publication: { id: uuid(), versionId: v.id, publishedAt: v.createdAt, authorLogin: null, authorName: author, documents: [] },
  });
  reservedSlugs.add(slug);
}
seed("welcome-to-observatory", "Observatory에 오신 것을 환영합니다", "스터디 그룹의 관찰과 기록을 위한 공간, Observatory를 소개합니다.", "2026-03-19", "관리자", ["공지", "소개"],
  "## Observatory란?\n\nObservatory는 스터디 그룹 멤버들이 각자 관심 있는 글을 읽고 정리하는 공간입니다.\n\n함께 읽고, 정리하고, 공유합시다!\n");
seed("understanding-transformer-architecture", "Transformer 아키텍처 이해하기", "Attention Is All You Need 논문의 핵심 개념을 정리합니다.", "2026-03-18", "김스터디", ["AI", "논문리뷰", "딥러닝"],
  "## Self-Attention\n\n```python\nscores = q @ k.T / sqrt(d)\n```\n\n| 구성 | 역할 |\n|---|---|\n| Encoder | 입력 표현 |\n| Decoder | 출력 생성 |\n");
seed("docker-basics-for-developers", "개발자를 위한 Docker 기초", "Docker의 핵심 개념과 실무에서 자주 쓰는 명령어를 정리합니다.", "2026-03-17", "박개발", ["DevOps", "Docker", "인프라"],
  "## 컨테이너란?\n\n`docker run --rm hello-world`\n\n<b>raw html</b>은 렌더하지 않습니다. ![외부](https://example.com/x.png)\n");

const published = () =>
  [...posts.values()]
    .filter((p) => p.publication)
    .sort((a, b) => (a.publication.publishedAt < b.publication.publishedAt ? 1 : a.publication.publishedAt > b.publication.publishedAt ? -1 : 0));

/** GET /public/posts·/public/posts/{slug}·/public/post-tags. 해당 없으면 null. */
export function handlePublicPosts(path, url) {
  if (path === "/public/posts") {
    const tag = url.searchParams.get("tag");
    const q = url.searchParams.get("q");
    let list = published().map((p) => publicView(p, false));
    if (tag !== null) list = list.filter((p) => p.tags.includes(tag));
    if (q) list = list.filter((p) => p.title.includes(q));
    return ok(200, page(list, url));
  }
  if (path === "/public/post-tags") {
    const counts = new Map();
    for (const p of published()) for (const t of publicView(p, false).tags) counts.set(t, (counts.get(t) ?? 0) + 1);
    const list = [...counts].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([tag, count]) => ({ tag, count }));
    return ok(200, page(list, url));
  }
  const m = /^\/public\/posts\/([^/]+)$/.exec(path);
  if (m) {
    const slug = decodeURIComponent(m[1]);
    const p = published().find((x) => x.slug === slug);
    return p ? ok(200, publicView(p)) : notFound();
  }
  return null;
}

// ---- 소유자 ----

const ownedPost = (owner, id) => {
  const p = posts.get(id);
  return p && p.owner === owner ? p : null;
};
const activeDraftOf = (owner, postId) => [...drafts.values()].find((d) => d.owner === owner && d.postId === postId && d.state === "active");
const byUpdated = (a, b) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0);

function previewOf(owner, p, v, docs) {
  const documents = v.documentIds.map((id) => {
    const d = docs.get(owner, id);
    return { id, title: d?.title ?? "", publicUrl: d?.published ? `${BASE}/public/document/?id=${id}` : null };
  });
  const imgs = imageIdsOf(v).map((id) => ({ id, url: imageUrl(id) }));
  const publishable = !p.archived && refsFields(owner, v, docs).length === 0;
  const state = JSON.stringify([p.version, v.id, documents, imgs]);
  const previewToken = crypto.createHash("sha256").update(state).digest("hex").slice(0, 32);
  const { id: versionId, title, content, description, slug, tags, coverImageId, documentIds } = v;
  return {
    postId: p.id, versionId, expectedVersion: p.version, previewToken, publishable,
    title, content, description, slug, tags, coverImageId, documentIds,
    author: { login: p.authorLogin }, documents, images: imgs,
    currentPublication: p.publication ? publicView(p) : null,
  };
}

/** GET 소유자 경로. 해당 없으면 null. docs: { get(owner,id), list(owner) } */
export function handlePostRead(owner, path, url, docs) {
  let m;
  if (path === "/research/posts") {
    const q = url.searchParams.get("q") ?? "";
    const list = [...posts.values()]
      .filter((p) => p.owner === owner && !p.archived && latest(p).title.includes(q))
      .sort(byUpdated)
      .map((p) => postView(p, false));
    return ok(200, page(list, url));
  }
  if (path === "/research/post-drafts") {
    const list = [...drafts.values()].filter((d) => d.owner === owner && d.state === "active").sort(byUpdated).map((d) => draftView(d, false));
    return ok(200, page(list, url));
  }
  if (path === "/research/post-document-options") {
    const q = url.searchParams.get("q") ?? "";
    const list = docs
      .list(owner)
      .filter((d) => !d.archived && d.title.includes(q))
      .map((d) => ({ id: d.id, title: d.title, version: d.version, latestVersionId: d.latestVersionId }));
    return ok(200, page(list, url));
  }
  if ((m = /^\/research\/post-drafts\/([^/]+)$/.exec(path))) {
    const d = drafts.get(m[1]);
    return d && d.owner === owner ? ok(200, draftView(d)) : notFound();
  }
  if (path === "/research/post-proposals") {
    const postId = url.searchParams.get("postId");
    if (postId && !ownedPost(owner, postId)) return notFound();
    const list = [...proposals.values()]
      .filter((p) => p.owner === owner && (!postId || p.postId === postId))
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
      .map((p) => draftView(p, false));
    return ok(200, page(list, url));
  }
  if ((m = /^\/research\/post-proposals\/([^/]+)$/.exec(path))) {
    const p = proposals.get(m[1]);
    return p && p.owner === owner ? ok(200, draftView(p)) : notFound();
  }
  if ((m = /^\/research\/posts\/([^/]+)(\/draft|\/versions|\/versions\/([^/]+)|\/publication-preview)?$/.exec(path))) {
    const p = ownedPost(owner, m[1]);
    if (!p) return notFound();
    if (!m[2]) return ok(200, postView(p));
    if (m[2] === "/draft") {
      const d = activeDraftOf(owner, p.id);
      return ok(200, { draft: d ? draftView(d) : null });
    }
    if (m[2] === "/versions") return ok(200, page([...p.versions].reverse().map((v) => versionView(p, v, false)), url));
    if (m[2] === "/publication-preview") {
      const vid = url.searchParams.get("versionId");
      const v = vid ? p.versions.find((x) => x.id === vid) : latest(p);
      return v ? ok(200, previewOf(owner, p, v, docs)) : notFound();
    }
    const v = p.versions.find((x) => x.id === m[3]);
    return v ? ok(200, versionView(p, v)) : notFound();
  }
  return null;
}

/** 변경 경로. 해당 없으면 null. user: 현재 계정(login). */
export function handlePostMutation(owner, method, path, b, user, docs) {
  let m;
  if (method === "POST" && path === "/research/post-drafts") {
    const fields = textFields(b, false);
    if (fields.length) return invalid(fields);
    let base = { postId: null, basePostVersion: null, baseVersionId: null };
    if (b.postId !== undefined && b.postId !== null) {
      const p = ownedPost(owner, b.postId);
      if (!p) return notFound();
      if (p.archived) return conflict("archived");
      if (activeDraftOf(owner, p.id)) return conflict("active_draft_exists");
      if (b.basePostVersion !== p.version) return conflict("post_version");
      if (b.baseVersionId !== latest(p).id) return conflict("base_changed");
      if (b.slug !== p.slug) return invalid([{ path: "body.slug", message: "slug is fixed" }]);
      base = { postId: p.id, basePostVersion: p.version, baseVersionId: latest(p).id };
    }
    const t = now();
    const d = { id: uuid(), owner, ...base, revision: 1, ...text(b), state: "active", createdAt: t, updatedAt: t };
    drafts.set(d.id, d);
    return ok(201, draftView(d));
  }
  if ((m = /^\/research\/post-drafts\/([^/]+)(\/commit)?$/.exec(path))) {
    const d = drafts.get(m[1]);
    if (!d || d.owner !== owner) return notFound();
    if (d.state !== "active") return conflict("draft_closed");
    if (b.expectedDraftRevision !== d.revision) return conflict("draft_revision");
    const p = d.postId ? ownedPost(owner, d.postId) : null;
    if (method === "DELETE" && !m[2]) {
      Object.assign(d, { state: "discarded", revision: d.revision + 1, updatedAt: now() });
      return ok(200, draftView(d));
    }
    if (method === "PUT" && !m[2]) {
      const fields = textFields(b, false);
      if (fields.length) return invalid(fields);
      if (p && b.slug !== p.slug) return invalid([{ path: "body.slug", message: "slug is fixed" }]);
      Object.assign(d, text(b), { revision: d.revision + 1, updatedAt: now() });
      return ok(200, draftView(d));
    }
    if (method === "POST" && m[2]) {
      const fields = textFields(d, true);
      if (fields.length) return invalid(fields);
      const refs = refsFields(owner, d, docs);
      if (refs.length) return invalid(refs);
      const t = now();
      let post = p;
      if (post) {
        if (post.archived) return conflict("archived");
        if (b.expectedVersion !== post.version) return conflict("post_version");
        const moved = post.version !== d.basePostVersion || latest(post).id !== d.baseVersionId;
        if (moved ? b.resolvedBaseVersionId !== latest(post).id : b.resolvedBaseVersionId !== undefined && b.resolvedBaseVersionId !== latest(post).id)
          return conflict("base_changed");
      } else {
        if (b.expectedVersion !== undefined || b.resolvedBaseVersionId !== undefined) return invalid([{ path: "body", message: "new drafts have no post base" }]);
        if (reservedSlugs.has(d.slug)) return conflict("slug_taken");
        post = { id: uuid(), owner, legacy: false, version: 0, archived: false, createdAt: t, updatedAt: t, authorLogin: user.login, authorName: user.login, versions: [], publication: null, slug: d.slug };
        posts.set(post.id, post);
        reservedSlugs.add(d.slug);
      }
      const { title, content, description, slug, tags, coverImageId, documentIds } = d;
      post.versions.push({ id: uuid(), number: post.versions.length + 1, title, content, description, slug, tags, coverImageId, documentIds: [...documentIds], createdAt: t });
      post.version++;
      post.updatedAt = t;
      Object.assign(d, { postId: post.id, state: "committed", revision: d.revision + 1, updatedAt: t });
      return ok(200, { draft: draftView(d), post: postView(post) });
    }
  }
  if (method === "POST" && (m = /^\/research\/post-proposals\/([^/]+)\/(accept|discard)$/.exec(path))) {
    const p = proposals.get(m[1]);
    if (!p || p.owner !== owner) return notFound();
    if (p.status !== "pending" || b.expectedProposalVersion !== p.version) return conflict("proposal_closed");
    if (m[2] === "discard") {
      Object.assign(p, { status: "discarded", version: p.version + 1, updatedAt: now() });
      return ok(200, draftView(p));
    }
    const fields = textFields(b, true);
    if (fields.length) return invalid(fields);
    const refs = refsFields(owner, b, docs);
    if (refs.length) return invalid(refs);
    let post = p.postId ? ownedPost(owner, p.postId) : null;
    let draft = null;
    if (post) {
      if (post.archived) return conflict("archived");
      if (b.expectedVersion !== post.version) return conflict("post_version");
      if (b.resolvedBaseVersionId !== latest(post).id) return conflict("base_changed");
      if (b.slug !== post.slug) return invalid([{ path: "body.slug", message: "slug is fixed" }]);
      draft = activeDraftOf(owner, post.id) ?? null;
    } else {
      if (b.expectedVersion !== undefined || b.resolvedBaseVersionId !== undefined) return invalid([{ path: "body", message: "new post proposal has no base" }]);
      if (p.baseDraftId) draft = drafts.get(p.baseDraftId) ?? null;
      else if (b.draftId) draft = drafts.get(b.draftId) ?? null;
    }
    if (draft) {
      if (b.draftId !== draft.id || draft.postId !== (post ? post.id : null)) return conflict("draft_revision");
      if (draft.state !== "active") return conflict("draft_closed");
      if (b.expectedDraftRevision !== draft.revision) return conflict("draft_revision");
    } else if (b.draftId !== undefined) return conflict("draft_closed");
    const t = now();
    if (!post) {
      if (reservedSlugs.has(b.slug)) return conflict("slug_taken");
      post = { id: uuid(), owner, legacy: false, version: 0, archived: false, createdAt: t, updatedAt: t, authorLogin: user.login, authorName: user.login, versions: [], publication: null, slug: b.slug };
      posts.set(post.id, post);
      reservedSlugs.add(b.slug);
    }
    const tx = text(b);
    post.versions.push({ id: uuid(), number: post.versions.length + 1, ...tx, createdAt: t });
    post.version++;
    post.updatedAt = t;
    if (draft) Object.assign(draft, { postId: post.id, state: "committed", revision: draft.revision + 1, updatedAt: t });
    Object.assign(p, { status: "accepted", acceptedPostId: post.id, acceptedVersionId: latest(post).id, version: p.version + 1, updatedAt: t });
    return ok(200, { proposal: draftView(p), post: postView(post), draft: draft ? draftView(draft) : null });
  }
  if ((m = /^\/research\/posts\/([^/]+)(\/publications|\/publication)?$/.exec(path))) {
    const p = ownedPost(owner, m[1]);
    if (!p) return notFound();
    if (b.expectedVersion !== p.version) return conflict("post_version");
    if (method === "DELETE" && !m[2]) {
      Object.assign(p, { archived: true, publication: null, version: p.version + 1, updatedAt: now() });
      return ok(200, postView(p, false));
    }
    if (method === "DELETE" && m[2] === "/publication") {
      Object.assign(p, { publication: null, version: p.version + 1, updatedAt: now() });
      return ok(200, { id: p.id, version: p.version, publication: null });
    }
    if (method === "POST" && m[2] === "/publications") {
      if (p.archived) return conflict("archived");
      const v = p.versions.find((x) => x.id === b.versionId);
      if (!v) return notFound();
      const preview = previewOf(owner, p, v, docs);
      if (b.previewToken !== preview.previewToken) return conflict("preview_changed");
      if (!preview.publishable) return invalid(refsFields(owner, v, docs));
      const t = now();
      p.publication = { id: uuid(), versionId: v.id, publishedAt: t, authorLogin: p.authorLogin, authorName: p.authorName, documents: preview.documents };
      Object.assign(p, { version: p.version + 1, updatedAt: t });
      return ok(201, publicView(p));
    }
  }
  return null;
}

/** 모의 Codex의 research_post_propose: 제안만 만들고 글·초안은 바꾸지 않는다. 대상이 없으면 null.
 *  target: { postId } 기존 글 / { draftId } 작성 중인 새 글 초안 / {} 새 글 */
export function createPostProposal(owner, target, patch) {
  const t = now();
  let base = { postId: null, basePostVersion: null, baseVersionId: null, baseDraftId: null, baseDraftRevision: null };
  let from = { title: "", content: "", description: "", slug: "", tags: [], coverImageId: null, documentIds: [] };
  if (target.postId) {
    const p = ownedPost(owner, target.postId);
    if (!p || p.archived) return null;
    base = { ...base, postId: p.id, basePostVersion: p.version, baseVersionId: latest(p).id };
    const { title, content, description, slug, tags, coverImageId, documentIds } = latest(p);
    from = { title, content, description, slug, tags, coverImageId, documentIds };
  } else if (target.draftId) {
    const d = drafts.get(target.draftId);
    if (!d || d.owner !== owner || d.state !== "active" || d.postId !== null) return null;
    base = { ...base, baseDraftId: d.id, baseDraftRevision: d.revision };
    const { title, content, description, slug, tags, coverImageId, documentIds } = d;
    from = { title, content, description, slug, tags, coverImageId, documentIds };
  }
  const row = { id: uuid(), owner, ...base, version: 1, ...from, ...patch(from), origin: "codex", status: "pending", acceptedPostId: null, acceptedVersionId: null, createdAt: t, updatedAt: t };
  proposals.set(row.id, row);
  return draftView(row, false);
}

/** 대화 페이지 문맥 확인용: 본인 활성 글 제목 / 현재 발행본 제목(postId 기준). 없으면 null. */
export function postTitle(owner, id) {
  const p = ownedPost(owner, id);
  return p && !p.archived ? latest(p).title : null;
}
export function publicPostTitle(postId) {
  const p = posts.get(postId);
  return p?.publication ? p.versions.find((v) => v.id === p.publication.versionId).title : null;
}
export function newPostDraftOf(owner, draftId) {
  const d = drafts.get(draftId);
  return d && d.owner === owner && d.state === "active" && d.postId === null ? d : null;
}

// ---- 이미지(D2) ----

// 1×1 투명 PNG(모의 원본). 실제 서버는 형식 검사·재인코딩한다.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const IMAGE_LIMIT = 5 * 1024 * 1024;

/** POST /research/post-images(원본 바이트). 형식·크기는 호출 측이 읽은 bytes로 판단한다. */
export function uploadImage(owner, type, bytes) {
  if (!IMAGE_TYPES.includes(type)) return fail(415, "unsupported_media_type", "Unsupported image type");
  if (bytes.length > IMAGE_LIMIT) return fail(413, "payload_too_large", "Image exceeds size limit");
  if (bytes.length === 0) return fail(422, "validation_error", "Image could not be decoded");
  const row = { id: uuid(), owner, mimeType: type, size: bytes.length, width: 1, height: 1, createdAt: now() };
  images.set(row.id, row);
  return ok(201, imageView(row));
}
const imageView = (r) => ({ id: r.id, url: imageUrl(r.id), mimeType: r.mimeType, size: r.size, width: r.width, height: r.height, createdAt: r.createdAt });
const imageInUse = (id) =>
  [...posts.values()].some((p) => p.versions.some((v) => imageIdsOf(v).includes(id))) ||
  [...drafts.values()].some((d) => imageIdsOf(d).includes(id)) ||
  [...proposals.values()].some((p) => imageIdsOf(p).includes(id));

export function handleImageRead(owner, path, url) {
  if (path !== "/research/post-images") return null;
  const list = [...images.values()].filter((i) => i.owner === owner).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1)).map(imageView);
  return ok(200, page(list, url));
}
export function handleImageDelete(owner, path) {
  const m = /^\/research\/post-images\/([^/]+)$/.exec(path);
  if (!m) return null;
  const img = images.get(m[1]);
  if (!img || img.owner !== owner) return notFound();
  if (imageInUse(img.id)) return conflict("image_in_use");
  images.delete(img.id);
  return ok(200, { id: img.id, deleted: true });
}
/** GET /media/post-images/{id}: 현재 발행본(본문·표지)에 쓰이면 누구나, 아니면 소유자만. 없으면 null(404). */
export function mediaBytes(owner, id) {
  const img = images.get(id);
  if (!img) return null;
  const isPublic = published().some((p) => imageIdsOf(p.versions.find((v) => v.id === p.publication.versionId)).includes(id));
  if (!isPublic && img.owner !== owner) return null;
  return { type: img.mimeType, bytes: PNG, isPublic };
}

export const postCounts = () => ({ posts: posts.size, postDrafts: drafts.size, published: published().length });
