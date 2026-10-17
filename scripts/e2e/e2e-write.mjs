// #28 글 쓰기 화면 검증: 모의 글 API(mock-posts.mjs) 뒤의 Next standalone을 실제 브라우저로 확인한다.
// 실제 backend/DB/Codex가 아니다(계약 형태만 흉내 낸다). 실제 API 검증은 e2e-research-offline.mjs(IP*)에서 한다.
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
async function appears(locator, timeout = 8000) {
  try {
    await locator.first().waitFor({ state: "visible", timeout });
    return true;
  } catch {
    return false;
  }
}
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

const browser = await chromium.launch();
const pageErrors = [];
const context = await browser.newContext();
const page = await context.newPage();
page.on("pageerror", (e) => pageErrors.push(e.message));
page.on("dialog", (d) => void d.accept());
const main = page.locator("main");
const chat = page.locator("#chat-panel");

async function csrf() {
  return (await (await page.request.get(U("/api/auth/me"))).json()).csrfToken;
}
const api = {
  async get(path) {
    return (await page.request.get(U(`/api${path}`))).json();
  },
  async send(method, path, data) {
    const r = await page.request.fetch(U(`/api${path}`), {
      method,
      data,
      headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID() },
    });
    return { status: r.status(), body: await r.json() };
  },
};
// 자동 저장 뒤 주소가 그 초안으로 바뀔 때까지 기다린다(상태 문구가 주소 교체보다 먼저 보일 수 있다).
const draftParam = async () => {
  await page.waitForURL(/[?&]draft=/, { timeout: 10000 }).catch(() => {});
  return new URL(page.url()).searchParams.get("draft");
};
const idParam = () => new URL(page.url()).searchParams.get("id");
const saved = () => main.getByText("초안 자동 저장됨").first().waitFor({ timeout: 10000 });
async function send(text) {
  await chat.getByLabel("메시지").fill(text);
  await chat.getByRole("button", { name: "보내기" }).click();
  await chat.getByText(`모의 응답: ${text}`).waitFor({ timeout: 15000 });
}
// 대화창은 페이지를 옮겨도 열린 대화를 유지할 수 있다. 메시지 입력이 없으면 목록에서 그 대화를 연다.
async function openChat(sessionTitle) {
  // 대화창은 이전 상태(열림)를 hydration 뒤에 되살린다. 그 사이 열기 버튼이 사라질 수 있어 짧게 기다린 뒤 판단한다.
  await page.waitForLoadState("networkidle").catch(() => {});
  if (!(await appears(chat, 1000))) await page.getByRole("button", { name: "Codex 대화창 열기" }).click({ timeout: 5000 }).catch(() => {});
  await chat.waitFor();
  if (!(await appears(chat.getByLabel("메시지"), 1500))) await chat.getByRole("link", { name: new RegExp(sessionTitle) }).first().click();
  await chat.getByLabel("메시지").waitFor();
}

await setMock("me=normal&health=normal&logout=normal&nextLogin=approved&access=approved&codex=fixture&window=&remaining=&runner=&runnerAll=");

// WP1. 비로그인
await page.goto(U("/write/"));
check("WP1. logged out /write/ asks login", await appears(main.getByText("로그인이 필요합니다")));

// WP2. 로그인 → 빈 목록
await page.goto(U("/"));
await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().click();
await page.locator("header").getByText("mock-user").first().waitFor();
await page.locator("header").getByRole("link", { name: "글 쓰기" }).first().click();
await page.waitForURL(U("/write/"));
check("WP2. /write/ is the post index (not 준비 중)", (await appears(main.getByRole("heading", { name: "글 쓰기" }))) && !(await main.innerText()).includes("준비 중"));
check("WP2. empty list notice", await appears(main.getByText("아직 저장한 글이 없습니다")));

// 연결할 문서(비공개) 준비
const docA = (await api.send("POST", "/research/documents", { title: "바탕 문서 A", content: "문서 A 본문 비밀 문장" })).body;
check("WP3. setup: document", !!docA.id, JSON.stringify(docA).slice(0, 120));

// WP3. 새 글: 주소 정리, 태그, 문서 연결·열람, 자체 이미지 미리보기, 자동 저장·복구, 저장
await main.getByRole("link", { name: "새 글 쓰기" }).click();
await page.waitForURL(U("/write/new/"));
await main.getByLabel("제목").fill("첫 글");
await main.getByLabel("주소(slug)").fill("First Post_1!");
check("WP3. slug normalized while typing", (await main.getByLabel("주소(slug)").inputValue()) === "first-post-1", await main.getByLabel("주소(slug)").inputValue());
const tagBox = main.getByLabel("태그", { exact: true });
await tagBox.fill("AI,");
await tagBox.fill("테스트");
await tagBox.press("Enter");
await tagBox.fill("AI");
await tagBox.press("Enter");
check("WP3. tags added, duplicate ignored", (await main.getByRole("button", { name: /^태그 .* 빼기$/ }).count()) === 2);
await main.getByLabel("요약").fill("첫 글 요약");
await main.getByRole("button", { name: "문서 연결" }).click();
await main.getByLabel("연결할 문서 찾기").fill("바탕");
await main.getByRole("button", { name: "바탕 문서 A 연결" }).click();
const links = main.getByRole("region", { name: "연결 문서" });
check("WP3. linked document listed", await appears(links.getByText("바탕 문서 A")));
await links.getByRole("button", { name: "열람" }).click();
check("WP3. linked document body readable", await appears(links.getByText("문서 A 본문 비밀 문장")));
const FAKE = "0f0e0d0c-0b0a-4908-8706-050403020100";
await main.getByLabel("본문").fill(`# 첫 글 본문\n\n![외부](https://example.com/x.png)\n\n![대문자](${BASE}/api/media/post-images/${FAKE.toUpperCase()})\n`);
// 이미지 올리기: 원본 바이트 POST → 본문 커서 위치에 Markdown 삽입
const imageBox = main.getByRole("region", { name: "이미지" });
await imageBox.getByLabel("이미지 파일").setInputFiles({ name: "그림 (1).png", mimeType: "image/png", buffer: PNG });
check("WP3. upload inserted note", await appears(imageBox.getByText("올렸습니다", { exact: false })));
const uploads = await api.get("/research/post-images");
const IMG = uploads.items[0]?.id;
check("WP3. upload stored (raw bytes, own image)", uploads.items.length === 1 && uploads.items[0].url === `${BASE}/api/media/post-images/${IMG}`, JSON.stringify(uploads.items));
check("WP3. markdown inserted with sanitized alt", (await main.getByLabel("본문").inputValue()).includes(`![그림  1](${BASE}/api/media/post-images/${IMG})`), await main.getByLabel("본문").inputValue());
const preview = main.getByRole("region", { name: "미리보기" });
await preview.getByRole("heading", { name: "첫 글 본문" }).waitFor();
await preview.locator("img").first().waitFor();
const imgs = await preview.locator("img").evaluateAll((els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
check("WP3. preview shows only exact own-image path as <img> (loads for owner)", imgs.length === 1 && imgs[0][0] === `${BASE}/api/media/post-images/${IMG}` && imgs[0][1] > 0, JSON.stringify(imgs));
check("WP3. external image stays a link", (await preview.getByRole("link", { name: "[이미지: 외부]" }).count()) === 1);
await imageBox.getByRole("button", { name: "표지로" }).first().click();
check("WP3. cover set from my images", await appears(imageBox.locator('img[alt="표지"]')));
// 올릴 수 없는 형식은 보내기 전에 막는다
await imageBox.getByLabel("이미지 파일").setInputFiles({ name: "a.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") });
check("WP3. gif refused client-side", await appears(imageBox.getByText("PNG, JPEG, WebP 이미지만", { exact: false })));
check("WP3. gif not uploaded", (await api.get("/research/post-images")).items.length === 1);
await saved();
const draftId = await draftParam();
check("WP3. autosave: address bound to draft", !!draftId);
const d1 = await api.get(`/research/post-drafts/${draftId}`);
check(
  "WP3. autosave: all post fields in draft",
  d1.slug === "first-post-1" && d1.coverImageId === IMG && JSON.stringify(d1.tags) === JSON.stringify(["AI", "테스트"]) && JSON.stringify(d1.documentIds) === JSON.stringify([docA.id]) && d1.description === "첫 글 요약" && d1.postId === null,
  JSON.stringify({ slug: d1.slug, tags: d1.tags, documentIds: d1.documentIds })
);
await page.reload();
await main.getByLabel("제목").waitFor();
check(
  "WP3. reload restores draft (title, slug, tags, documents)",
  (await main.getByLabel("제목").inputValue()) === "첫 글" &&
    (await main.getByLabel("주소(slug)").inputValue()) === "first-post-1" &&
    (await main.getByRole("button", { name: "태그 테스트 빼기" }).count()) === 1 &&
    (await appears(links.getByText("바탕 문서 A")))
);
await main.getByRole("button", { name: "글 저장" }).click();
await page.waitForURL(/\/write\/\?id=/);
const postId = idParam();
check("WP3. commit opens post page", await appears(main.getByRole("heading", { name: "첫 글" })));
const p1 = await api.get(`/research/posts/${postId}`);
check("WP3. commit: v1 with fields, draft closed", p1.latestVersion.number === 1 && p1.latestVersion.slug === "first-post-1" && (await api.get(`/research/post-drafts/${draftId}`)).state === "committed");

// WP4. 저장 전 주소 확인(끝 하이픈은 초안에는 남지만 저장은 막는다)
await page.goto(U("/write/new/"));
await main.getByLabel("제목").fill("주소 확인");
await main.getByLabel("주소(slug)").fill("bad-");
await main.getByLabel("본문").fill("본문");
await saved();
check("WP4. draft keeps trailing hyphen while typing", (await api.get(`/research/post-drafts/${await draftParam()}`)).slug === "bad-");
await main.getByRole("button", { name: "글 저장" }).click();
check("WP4. strict slug checked before commit", await appears(main.getByText("주소(slug)를 소문자 영문·숫자와 중간 하이픈으로", { exact: false })));
await main.getByLabel("주소(slug)").fill("first-post-1");
await saved();
await main.getByRole("button", { name: "글 저장" }).click();
check("WP4. taken slug -> slug_taken message", await appears(main.getByText("이미 쓰였거나 예약된 주소(slug)입니다", { exact: false })));
await main.getByRole("button", { name: "초안 버리기…" }).click();
await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
await main.getByText("초안을 버렸습니다").waitFor();

// WP5. 기존 글 수정: 주소는 읽기 전용, 새 버전, 버전 비교에 글 정보 변경
await page.goto(U(`/write/?id=${postId}`));
await main.getByRole("button", { name: "내용 수정" }).click();
check("WP5. slug read-only for saved post", (await main.getByLabel("주소(slug)").getAttribute("readonly")) !== null);
await main.getByLabel("본문").fill("# 첫 글 본문\n\n두 번째 버전 문장");
await main.getByLabel("태그", { exact: true }).fill("v2");
await main.getByLabel("태그", { exact: true }).press("Enter");
await saved();
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
await main.getByRole("button", { name: "내용 수정" }).waitFor();
check("WP5. new version saved", (await api.get(`/research/posts/${postId}`)).latestVersion.number === 2);
await main.getByRole("button", { name: "버전 비교" }).click();
const cmp = main.getByRole("region", { name: "버전 비교" });
check("WP5. version compare shows body diff", await appears(cmp.locator('[data-diff="add"]', { hasText: "두 번째 버전 문장" })));
check("WP5. version compare shows meta diff (+tag)", await appears(cmp.getByLabel("글 정보 변경").getByText("+v2")));
await cmp.getByRole("button", { name: "비교 닫기" }).click();

// WP6. 발행: v1 미리보기 → 문서 제목 공개 안내 → 발행 → 비로그인 공개 화면
const pub = main.getByRole("region", { name: "발행" });
check("WP6. not published yet", await appears(pub.getByText("발행하지 않은 글입니다")));
const v1Id = p1.latestVersion.id;
await pub.getByLabel("발행할 버전").selectOption(v1Id);
await pub.getByRole("button", { name: "발행 미리보기" }).click();
check("WP6. preview warns linked document title becomes public", await appears(pub.getByText("함께 공개되는 문서 제목 (1)")));
check("WP6. preview marks private document as title only", await appears(pub.getByText(/바탕 문서 A — 제목만 표시/)));
check("WP6. page preview renders the article", await appears(pub.locator("article h1", { hasText: "첫 글" })));
await pub.getByRole("button", { name: "이 버전 발행" }).click();
check("WP6. published note", await appears(pub.getByText("발행했습니다", { exact: false })));
check("WP6. status shows published v1 not latest", await appears(pub.getByText("최신 버전 v2은 아직 발행하지 않았습니다", { exact: false })));
{
  const anon = await (await browser.newContext()).newPage();
  await anon.goto(U("/posts/first-post-1/"));
  await anon.locator("article h1").first().waitFor();
  const text = await anon.locator("main").innerText();
  check("WP6. public page shows v1 (not v2)", text.includes("첫 글 본문") && !text.includes("두 번째 버전 문장"));
  check("WP6. public page lists based-on document title, no link, no body", text.includes("바탕이 된 참고 문서") && text.includes("바탕 문서 A") && !text.includes("문서 A 본문 비밀 문장") && (await anon.getByRole("link", { name: "바탕 문서 A" }).count()) === 0);
  await anon.locator("article .prose img").first().waitFor().catch(() => {});
  const pubImgs = await anon.locator("article img").evaluateAll((els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
  check("WP6. public page: cover + one own body image, both load anonymously", pubImgs.length === 2 && pubImgs.every(([src, w]) => src === `${BASE}/api/media/post-images/${IMG}` && w > 0), JSON.stringify(pubImgs));
  const media = await anon.request.get(U(`/api/media/post-images/${IMG}`));
  check("WP6. published image public (revalidate cache)", media.status() === 200 && (media.headers()["cache-control"] ?? "").includes("must-revalidate"), `${media.status()} ${media.headers()["cache-control"]}`);
  await anon.goto(U("/"));
  check("WP6. home lists new post first", await appears(anon.locator("main article").first().getByText("첫 글")));
  await anon.goto(U(`/tags/${encodeURIComponent("테스트")}/`));
  check("WP6. tag page lists new post", await appears(anon.getByRole("link", { name: /첫 글/ })));
  await anon.context().close();
}

// WP7. 미리보기 뒤에 글이 바뀌면 발행하지 않고 다시 확인하게 한다
await pub.getByLabel("발행할 버전").selectOption((await api.get(`/research/posts/${postId}`)).latestVersion.id);
await pub.getByRole("button", { name: "발행 미리보기" }).click();
await pub.getByRole("button", { name: "현재 공개본과 비교" }).waitFor();
check("WP7. republish preview diffs against current publication", await appears(pub.locator('[data-diff="add"]', { hasText: "두 번째 버전 문장" })));
{
  // 다른 탭에서 발행 취소(글 version 증가)
  const cur = await api.get(`/research/posts/${postId}`);
  await api.send("DELETE", `/research/posts/${postId}/publication`, { expectedVersion: cur.version });
  await pub.getByRole("button", { name: "이 버전으로 다시 발행" }).click();
  check("WP7. stale preview not published (conflict shown)", await appears(pub.getByText(/글이 그사이 바뀌었습니다|미리보기 뒤에/)));
  check("WP7. stale preview: still unpublished on server", (await api.get(`/research/posts/${postId}`)).publication === null);
}
// 충돌 뒤에는 새 상태(발행 취소됨)로 미리보기를 다시 보여 준다. 다시 확인하고 발행한다.
await pub.getByRole("button", { name: "이 버전 발행" }).click();
check("WP7. publish v2 after re-preview", await appears(pub.getByText("발행했습니다", { exact: false })));
check("WP7. server publication is v2", (await api.get(`/research/posts/${postId}`)).publication?.versionId === (await api.get(`/research/posts/${postId}`)).latestVersion.id);

// WP8. Codex 글 수정 제안: 대화 → 제안 검토 링크 → 비교 → 수락(새 버전 1개). 발행본은 그대로.
const sess = await api.send("POST", "/research/sessions", { title: "글 대화" });
check("WP8. setup: session", sess.status === 201, String(sess.status));
await page.goto(U(`/write/?id=${postId}`));
await openChat("글 대화");
check("WP8. chat scope: this post", await appears(chat.getByText("보고 있는 화면: 이 글")));
const before8 = await api.get(`/research/posts/${postId}`);
await send("/fixture/post-propose 다듬어줘");
check("WP8. chat explains post unchanged", await appears(chat.getByText("Codex가 글 수정 제안을 만들었습니다", { exact: false })));
check("WP8. post unchanged by proposal", (await api.get(`/research/posts/${postId}`)).latestVersion.number === before8.latestVersion.number);
await chat.getByRole("link", { name: "제안 검토" }).last().click();
const proposals = main.locator("#proposals");
check("WP8. proposal diff vs current post", await appears(proposals.locator('[data-diff="add"]', { hasText: "Codex가 덧붙인 문단" })));
check("WP8. proposal meta diff (+codex tag)", await appears(proposals.getByLabel("글 정보 변경").getByText("+codex")));
await proposals.getByRole("button", { name: "제안 수락" }).click();
await proposals.getByRole("button", { name: "수락 확인" }).click();
check("WP8. accepted: shown as latest", await appears(main.locator(".prose").getByText("Codex가 덧붙인 문단")));
const after8 = await api.get(`/research/posts/${postId}`);
check("WP8. accepted: exactly one version added", after8.latestVersion.number === before8.latestVersion.number + 1);
check("WP8. accepted: publication unchanged", after8.publication?.versionId === before8.publication?.versionId);

// WP9. 작성 중인 새 글 초안에 대한 Codex 제안 → 주소 정하고 수락 → 새 글, 초안 닫힘
await page.goto(U("/write/new/"));
await main.getByLabel("제목").fill("초안 글");
await main.getByLabel("본문").fill("사람이 쓴 첫 문단");
await saved();
const draft9 = await draftParam();
await openChat("글 대화");
check("WP9. chat scope: write workspace", await appears(chat.getByText("보고 있는 화면: 글 쓰기")));
await send("/fixture/post-propose 이어서 써줘");
const list9 = await api.get("/research/post-proposals");
const prop9 = list9.items.find((p) => p.baseDraftId === draft9);
check("WP9. proposal bound to the open draft (page.draftId sent)", !!prop9 && prop9.postId === null, JSON.stringify(list9.items.map((p) => p.baseDraftId)));
await page.reload();
check("WP9. editor lists proposal for this draft", await appears(main.getByRole("region", { name: "Codex 새 글 제안" }).getByRole("link", { name: /초안 글 \(제안\) — 검토/ })));
await openChat("글 대화");
await chat.getByRole("link", { name: "제안 검토" }).last().click();
await page.waitForURL(/\/write\/new\/\?proposal=/);
check("WP9. review diffs from the draft", await appears(main.locator('[data-diff="add"]', { hasText: "Codex가 덧붙인 문단" })));
await main.getByRole("button", { name: "제안 수락" }).click();
await main.getByRole("button", { name: "수락 확인" }).click();
check("WP9. slug required before accepting new post", await appears(main.getByText("주소(slug)를 소문자 영문·숫자와 중간 하이픈으로 정한 뒤 수락하세요")));
await main.getByLabel("주소(slug)").fill("codex-new-post");
await main.getByRole("button", { name: "수락 확인" }).click();
await page.waitForURL(/\/write\/\?id=/);
check("WP9. accepted new post opens", await appears(main.getByRole("heading", { name: "초안 글 (제안)" })));
check("WP9. base draft closed", (await api.get(`/research/post-drafts/${draft9}`)).state === "committed");

// WP10. 초안 없이 제안된 새 글 → 목록 → 버리기
await page.goto(U("/write/"));
await openChat("글 대화");
await send("/fixture/post-propose 새 글 하나");
await page.reload();
const newBox = main.getByRole("region", { name: "Codex 새 글 제안" });
check("WP10. index lists new post proposal", await appears(newBox.getByRole("link", { name: /Codex가 쓴 새 글 — 검토/ })));
await newBox.getByRole("link", { name: /Codex가 쓴 새 글 — 검토/ }).click();
await main.getByRole("button", { name: "제안 버리기" }).click();
await page.waitForURL(U("/write/"));
check("WP10. discarded: no post created", !(await api.get("/research/posts")).items.some((p) => p.latestVersion.title === "Codex가 쓴 새 글"));

// WP11. D1: research_document_save는 새 문서 제안 → 검토 → 수락해야 문서가 생긴다
await page.goto(U("/research/?tab=documents"));
await openChat("글 대화");
const docsBefore = (await api.get("/research/documents")).items.length;
await send("/fixture/doc-save 정리해줘");
check("WP11. chat: new document proposal", await appears(chat.getByText("Codex가 새 문서를 제안했습니다", { exact: false })));
check("WP11. no document created yet", (await api.get("/research/documents")).items.length === docsBefore);
await chat.getByRole("link", { name: "제안 검토" }).last().click();
await page.waitForURL(/\/research\/document\/new\/\?proposal=/);
await main.getByRole("button", { name: "제안 수락(새 문서 만들기)" }).click();
await page.waitForURL(/\/research\/document\/\?id=/);
check("WP11. accepted: document created", await appears(main.getByRole("heading", { name: "Codex 새 문서" })));
check("WP11. exactly one document added", (await api.get("/research/documents")).items.length === docsBefore + 1);

// WP12. 공개 글 화면의 대화 범위는 이 발행 글(postId)
await page.goto(U("/posts/first-post-1/"));
await page.locator("article h1").first().waitFor();
await openChat("글 대화");
check("WP12. public post scope: bounded", await appears(chat.getByText("다음 질문 범위: 이 발행 글")));

// WP12b. 쓰는 이미지는 지울 수 없고, 쓰지 않는 이미지는 지울 수 있다
await page.goto(U(`/write/?id=${postId}`));
await main.getByRole("button", { name: "내용 수정" }).click();
const imgPanel = main.getByRole("region", { name: "이미지" });
await imgPanel.getByLabel("이미지 파일").setInputFiles({ name: "spare.webp", mimeType: "image/webp", buffer: Buffer.from("RIFF0000WEBPVP8 ") });
await imgPanel.getByText("올렸습니다", { exact: false }).waitFor();
// 방금 넣은 이미지 줄은 본문에서 다시 빼서 쓰지 않는 이미지로 만든다
const withSpare = await main.getByLabel("본문").inputValue();
await main.getByLabel("본문").fill(withSpare.replace(/\n*!\[spare\]\([^)]*\)\n?/, "\n"));
await saved();
const spare = (await api.get("/research/post-images")).items.find((i) => i.mimeType === "image/webp");
await imgPanel.locator("li").filter({ has: page.locator(`img[src$="${IMG}"]`) }).getByRole("button", { name: "지우기" }).click();
check("WP12b. in-use image delete refused", await appears(imgPanel.getByText("쓰고 있는 이미지라 지울 수 없습니다", { exact: false })));
await imgPanel.locator("li").filter({ has: page.locator(`img[src$="${spare.id}"]`) }).getByRole("button", { name: "지우기" }).click();
let spareGone = false;
for (let i = 0; i < 20 && !spareGone; i++) {
  spareGone = !(await api.get("/research/post-images")).items.some((x) => x.id === spare.id);
  if (!spareGone) await page.waitForTimeout(300);
}
check("WP12b. unused image deleted", spareGone);
await main.getByRole("button", { name: "초안 버리기…" }).click();
await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
await main.getByRole("button", { name: "내용 수정" }).waitFor();

// WP13. 발행 취소·보관
await page.goto(U(`/write/?id=${postId}`));
await pub.getByRole("button", { name: "발행 취소…" }).click();
await pub.getByRole("button", { name: "발행 취소", exact: true }).click();
check("WP13. unpublished note", await appears(pub.getByText("발행을 취소했습니다", { exact: false })));
{
  const anon = await (await browser.newContext()).newPage();
  await anon.goto(U("/posts/first-post-1/"));
  check("WP13. unpublished post not found publicly", await appears(anon.getByText("글을 찾을 수 없습니다")));
  check("WP13. unpublished image no longer public", (await anon.request.get(U(`/api/media/post-images/${IMG}`))).status() === 404);
  await anon.context().close();
}
await main.getByRole("button", { name: "보관", exact: true }).click();
check("WP13. archived: notice on the post page", await appears(main.getByText("보관한 글입니다", { exact: false })));
await page.goto(U("/write/"));
await main.getByRole("heading", { name: "내 글" }).waitFor();
await main.getByText("불러오는 중...").waitFor({ state: "detached" }).catch(() => {});
check("WP13. archived post leaves the list", !(await main.innerText()).includes("첫 글 (제안)"));

check("no page errors", pageErrors.length === 0, pageErrors.join(" | "));
await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\nRESULT ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
