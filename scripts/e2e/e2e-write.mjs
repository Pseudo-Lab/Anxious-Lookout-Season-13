// #28(+#31 W1–W3) 글 쓰기 화면 검증: 모의 글 API(mock-posts.mjs) 뒤의 Next standalone을 실제 브라우저로 확인한다.
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
// 대화상자는 기본으로 수락한다. dismissNext면 다음 하나만 취소한다(떠날 때 확인에서 "머무르기" 선택).
let dismissNext = false;
const dialogsSeen = [];
page.on("dialog", (d) => {
  dialogsSeen.push(d.type());
  if (dismissNext) {
    dismissNext = false;
    void d.dismiss();
  } else void d.accept();
});
const leaveGuarded = () =>
  page.evaluate(() => {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  });
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
      // #36 본문 쓰기는 계약 revision을 밝힌다(다른 요청에는 영향 없음)
      headers: { Origin: ORIGIN, "X-CSRF-Token": await csrf(), "Idempotency-Key": crypto.randomUUID(), "X-Post-Contract-Revision": "1.1" },
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
// #32 본문은 블록 편집기다. 전부 지우고 줄마다 입력한다(입력 규칙: "# " 제목, **굵게**, `코드`). 줄 사이는 Enter(새 문단).
const bodyBox = () => main.locator(".ProseMirror");
async function writeBody(lines) {
  await bodyBox().waitFor({ timeout: 15000 });
  await bodyBox().click();
  await page.keyboard.press("Control+a");
  await page.keyboard.press("Delete");
  await page.keyboard.press("Control+Alt+0");
  for (const [i, line] of lines.entries()) {
    if (i) await page.keyboard.press("Enter");
    await page.keyboard.type(line);
  }
}
async function pasteHtml(html) {
  await bodyBox().evaluate((el, h) => {
    const dt = new DataTransfer();
    dt.setData("text/html", h);
    dt.setData("text/plain", "");
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
  }, html);
}
const blockTypes = (content) => (content?.blocks ?? []).map((b) => b.type);
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

// 연결할 문서(비공개)와 그 문서가 가리키는 자료(#31 W3) 준비
const docA = (await api.send("POST", "/research/documents", { title: "바탕 문서 A", content: "문서 A 본문 비밀 문장" })).body;
check("WP3. setup: document", !!docA.id, JSON.stringify(docA).slice(0, 120));
const material = async (title, sourceUrl, content) =>
  (await api.send("POST", "/research/materials", { title, sourceUrl, collectedAt: "2026-10-18T00:00:00Z", contentKind: "excerpt", content })).body;
const relate = (docId, materialId) => api.send("POST", "/research/relations", { source: { type: "document", id: docId }, target: { type: "material", id: materialId }, kind: "근거", directed: true });
const matA = await material("문서 A의 자료", "https://example.com/source-a", "자료 A 본문 비밀 메모");
const matB = await material("직접 고른 자료", "https://example.com/source-b", "자료 B 본문 비밀 메모");
check("WP3. setup: materials + document->material relation", !!matA.id && !!matB.id && (await relate(docA.id, matA.id)).status === 201);

// WP3. 새 글: 주소 입력 없음(#31 W1), 요약은 접힘(W2), 태그, 문서 연결·열람과 자료 자동 포함·직접 추가(W3), 자체 이미지 미리보기, 자동 저장·복구, 저장
await main.getByRole("link", { name: "새 글 쓰기" }).click();
await page.waitForURL(U("/write/new/"));
await main.getByLabel("제목").fill("첫 글");
check("WP3. W1: no slug input; address is assigned on first save", (await main.getByLabel(/주소\(slug\)/).count()) === 0 && (await appears(main.getByText("처음 저장할 때 자동으로 정해지며", { exact: false }))));
check("WP3. W2: summary collapsed by default", (await main.getByRole("button", { name: "요약 추가(선택)" }).isVisible()) && (await main.getByLabel("요약").count()) === 0);
const tagBox = main.getByLabel("태그", { exact: true });
await tagBox.fill("AI,");
await tagBox.fill("테스트");
await tagBox.press("Enter");
await tagBox.fill("AI");
await tagBox.press("Enter");
check("WP3. tags added, duplicate ignored", (await main.getByRole("button", { name: /^태그 .* 빼기$/ }).count()) === 2);
await main.getByRole("button", { name: "요약 추가(선택)" }).click();
await main.getByLabel("요약").fill("첫 글 요약");
// 자료 찾기는 읽기 전용 POST: CSRF만 보내고 Idempotency-Key는 보내지 않는다
const resolveRequests = [];
page.on("request", (r) => {
  if (r.url().includes("/api/research/post-material-options/resolve")) resolveRequests.push({ headers: r.headers(), body: r.postDataJSON() });
});
await main.getByRole("button", { name: "문서 연결" }).click();
await main.getByLabel("연결할 문서 찾기").fill("바탕");
await main.getByRole("button", { name: "바탕 문서 A 연결" }).click();
const links = main.getByRole("region", { name: "연결 문서" });
check("WP3. linked document listed", await appears(links.getByText("바탕 문서 A")));
const mats = main.getByRole("region", { name: "바탕이 된 자료" });
check("WP3. W3: linking a document adds its material (auto)", await appears(mats.locator("li", { hasText: "문서 A의 자료" }).getByText("문서에서 자동 포함", { exact: false })));
check(
  "WP3. W3: resolve sent with CSRF, without Idempotency-Key",
  resolveRequests.length === 1 && !!resolveRequests[0].headers["x-csrf-token"] && !("idempotency-key" in resolveRequests[0].headers) && JSON.stringify(resolveRequests[0].body) === JSON.stringify({ documentIds: [docA.id] }),
  JSON.stringify(resolveRequests.map((r) => [Object.keys(r.headers), r.body]))
);
await mats.getByRole("button", { name: "자료 추가" }).click();
await mats.getByLabel("추가할 자료 찾기").fill("직접");
await mats.getByRole("button", { name: "직접 고른 자료 추가" }).click();
check("WP3. W3: material added directly", await appears(mats.locator("li", { hasText: "직접 고른 자료" }).getByText("직접 추가")));
check("WP3. W3: source URL shown", await appears(mats.getByRole("link", { name: "https://example.com/source-a" })));
await links.getByRole("button", { name: "열람" }).click();
check("WP3. linked document body readable", await appears(links.getByText("문서 A 본문 비밀 문장")));
await writeBody(["# 첫 글 본문"]);
await page.keyboard.press("Enter");
// 외부 이미지·iframe이 섞인 HTML을 붙여 넣어도 글자만 들어온다(이미지는 자체 업로드 imageId로만)
await pasteHtml('<p>붙인 글<img src="https://example.com/x.png"></p><iframe src="https://example.com/frame"></iframe>');
// 이미지 올리기: 원본 바이트 POST → 본문 커서 위치에 이미지 블록(imageId) 삽입
const imageBox = main.getByRole("region", { name: "이미지" });
await imageBox.getByLabel("이미지 파일").setInputFiles({ name: "그림 (1).png", mimeType: "image/png", buffer: PNG });
check("WP3. upload inserted note", await appears(imageBox.getByText("올렸습니다", { exact: false })));
const uploads = await api.get("/research/post-images");
const IMG = uploads.items[0]?.id;
check("WP3. upload stored (raw bytes, own image)", uploads.items.length === 1 && uploads.items[0].url === `${BASE}/api/media/post-images/${IMG}`, JSON.stringify(uploads.items));
const figure = bodyBox().locator("figure[data-type=post-image]");
await figure.locator("img").first().waitFor();
const imgs = await bodyBox().locator("img").evaluateAll((els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
check("WP3. #32: image block inserted by imageId (own image loads for owner)", imgs.length === 1 && imgs[0][0] === `${BASE}/api/media/post-images/${IMG}` && imgs[0][1] > 0, JSON.stringify(imgs));
check("WP3. #32: alt text taken from the file name", (await figure.getByLabel("대체 텍스트").inputValue()) === "그림 (1)");
check("WP3. #32: pasted external image/iframe not inserted, text kept", (await bodyBox().locator("iframe").count()) === 0 && (await bodyBox().innerText()).includes("붙인 글"));
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
  "WP3. #32: draft content is a post-blocks document (heading, pasted text, image by id)",
  d1.content?.format === "post-blocks" &&
    blockTypes(d1.content).join(",") === "heading,paragraph,image" &&
    d1.content.blocks[2].imageId === IMG &&
    d1.textProjection.startsWith("첫 글 본문\n\n붙인 글"),
  JSON.stringify({ types: blockTypes(d1.content), projection: d1.textProjection })
);
check(
  "WP3. autosave: all post fields in draft (no slug, explicit materials)",
  d1.slug === "" &&
    d1.coverImageId === IMG &&
    JSON.stringify(d1.tags) === JSON.stringify(["AI", "테스트"]) &&
    JSON.stringify(d1.documentIds) === JSON.stringify([docA.id]) &&
    JSON.stringify(d1.materialIds) === JSON.stringify([matA.id, matB.id]) &&
    d1.description === "첫 글 요약" &&
    d1.postId === null,
  JSON.stringify({ slug: d1.slug, tags: d1.tags, documentIds: d1.documentIds, materialIds: d1.materialIds })
);
await page.reload();
await main.getByLabel("제목").waitFor();
check(
  "WP3. reload restores draft (title, summary, tags, documents, materials)",
  (await main.getByLabel("제목").inputValue()) === "첫 글" &&
    (await main.getByLabel("요약").inputValue()) === "첫 글 요약" &&
    (await appears(bodyBox().getByRole("heading", { name: "첫 글 본문" }))) &&
    (await main.getByRole("button", { name: "태그 테스트 빼기" }).count()) === 1 &&
    (await appears(links.getByText("바탕 문서 A"))) &&
    (await appears(mats.locator("li", { hasText: "문서 A의 자료" }).getByText("문서에서 자동 포함", { exact: false }))) &&
    (await appears(mats.locator("li", { hasText: "직접 고른 자료" }).getByText("직접 추가")))
);
await main.getByRole("button", { name: "글 저장" }).click();
await page.waitForURL(/\/write\/\?id=/);
const postId = idParam();
check("WP3. commit opens post page", await appears(main.getByRole("heading", { name: "첫 글" })));
const p1 = await api.get(`/research/posts/${postId}`);
const slug1 = p1.latestVersion.slug;
check(
  "WP3. commit: v1, server-generated slug, material sources, draft closed",
  p1.latestVersion.number === 1 &&
    /^post-[0-9a-f]{32}$/.test(slug1) &&
    JSON.stringify(p1.latestVersion.materialSources) === JSON.stringify([{ materialId: matA.id, documentIds: [docA.id] }, { materialId: matB.id, documentIds: [] }]) &&
    (await api.get(`/research/post-drafts/${draftId}`)).state === "committed",
  JSON.stringify({ slug1, sources: p1.latestVersion.materialSources })
);
check("WP3. W1: post page shows the public address", await appears(main.getByText(`주소 /posts/${slug1}/`, { exact: false })));

// WP3b. 리뷰 F1: 업로드를 기다리는 동안 바꾼 제목·본문·메타를 업로드 완료가 되돌리지 않는다. 그동안 저장·버리기는 잠긴다.
{
  await page.goto(U("/write/new/"));
  await main.getByLabel("제목").fill("업로드 전 제목");
  await writeBody(["업로드 전 본문"]);
  let release;
  const held = new Promise((r) => (release = r));
  const hold = async (route) => {
    if (route.request().method() === "POST") await held;
    return route.continue().catch(() => {});
  };
  await page.route("**/api/research/post-images", hold);
  const box = main.getByRole("region", { name: "이미지" });
  await box.getByLabel("이미지 파일").setInputFiles({ name: "slow.png", mimeType: "image/png", buffer: PNG });
  await main.getByText("이미지를 올리는 중입니다", { exact: false }).waitFor();
  check("WP3b. save/discard locked during upload", (await main.getByRole("button", { name: "글 저장" }).isDisabled()) && (await main.getByRole("button", { name: "초안 버리기…" }).isDisabled()));
  await main.getByLabel("제목").fill("업로드 중 바꾼 제목");
  await writeBody(["업로드 중 바꾼 본문"]);
  await main.getByRole("button", { name: "요약 추가(선택)" }).click();
  await main.getByLabel("요약").fill("업로드 중 요약");
  await main.getByLabel("태그", { exact: true }).fill("업로드중");
  await main.getByLabel("태그", { exact: true }).press("Enter");
  release();
  await box.getByText("올렸습니다", { exact: false }).waitFor();
  await page.unroute("**/api/research/post-images", hold);
  const body = await bodyBox().innerText();
  check(
    "WP3b. latest title/body/meta kept, image inserted",
    (await main.getByLabel("제목").inputValue()) === "업로드 중 바꾼 제목" &&
      body.startsWith("업로드 중 바꾼 본문") &&
      (await bodyBox().locator("figure[data-type=post-image]").getByLabel("대체 텍스트").inputValue()) === "slow" &&
      (await main.getByLabel("요약").inputValue()) === "업로드 중 요약" &&
      (await main.getByRole("button", { name: "태그 업로드중 빼기" }).count()) === 1,
    JSON.stringify(body)
  );
  check("WP3b. save unlocked after upload", !(await main.getByRole("button", { name: "글 저장" }).isDisabled()));
  await saved();
  const d = await api.get(`/research/post-drafts/${await draftParam()}`);
  check(
    "WP3b. server draft has the latest input",
    d.title === "업로드 중 바꾼 제목" && d.description === "업로드 중 요약" && d.textProjection.startsWith("업로드 중 바꾼 본문") && blockTypes(d.content).includes("image"),
    JSON.stringify({ projection: d.textProjection, types: blockTypes(d.content) })
  );
  await main.getByRole("button", { name: "초안 버리기…" }).click();
  await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
  await main.getByText("초안을 버렸습니다").waitFor();
}

// WP3c. 리뷰 M1: 상한을 넘은 본문(화면 입력 ≠ 저장 가능한 본문)은 고칠 때까지 새 글 저장을 막고, 떠날 때 확인한다.
//        서버 초안은 마지막 유효 본문이며, 고친 뒤 저장한 버전은 화면에 보이던 본문과 같다.
{
  await page.goto(U("/write/new/"));
  await main.getByLabel("제목").fill("상한 회귀");
  await writeBody(["kept-valid"]);
  await saved();
  const validDraft = await draftParam();
  // 본문에 표식 문장을 넣어 브라우저 저장소에 본문이 남지 않는지도 확인한다(리뷰 round3 M3)
  await page.keyboard.insertText("민감한표식" + "x".repeat(20001));
  const reason = main.getByText("저장할 수 있는 크기·개수 상한을 넘었습니다", { exact: false });
  check("WP3c. M1: over-limit body shows the reason", await appears(reason));
  check("WP3c. M1: save blocked while the body is invalid", await main.getByRole("button", { name: "글 저장" }).isDisabled());
  check("WP3c. M1: explicit discard stays available", !(await main.getByRole("button", { name: "초안 버리기…" }).isDisabled()));
  const prevented = await page.evaluate(() => {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  });
  check("WP3c. M1: leaving the page asks for confirmation", prevented);
  const kept = await api.get(`/research/post-drafts/${validDraft}`);
  check("WP3c. M1: server draft keeps the last valid body (not the over-limit input)", kept.textProjection === "kept-valid", JSON.stringify(kept.textProjection?.slice(0, 40)));
  // round2: 실제 내부 Link 이동 → 뒤로가기, 그리고 새로고침 뒤에도 저장 못 한 본문을 경고와 함께 되살린다
  const restoredOk = async () =>
    (await appears(reason)) &&
    (await bodyBox().innerText()).includes("kept-valid민감한표식" + "x".repeat(100)) &&
    (await main.getByRole("button", { name: "글 저장" }).isDisabled());
  // round3 M1: 브라우저 저장소가 가득 차 쓰기가 실패해도(QuotaExceededError) 내부 이동 뒤 입력을 잃지 않는다
  await page.evaluate(() => {
    window.__setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function () {
      throw new DOMException("quota", "QuotaExceededError");
    };
  });
  await main.getByRole("link", { name: "← 내 글" }).click();
  await page.waitForURL(U("/write/"));
  await page.goBack();
  await page.waitForURL(/\/write\/new\/\?draft=/);
  check("WP3c. M1: after an in-app Link and Back, the unsaved body is restored with the warning and save stays blocked", await restoredOk());
  check("WP3c. M1: restore notice shown", await appears(main.getByText("저장하지 못하고 떠났던 본문을 되살렸습니다", { exact: false })));
  await page.evaluate(() => {
    Storage.prototype.setItem = window.__setItem;
  });
  const atRest = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  check("WP3c. M3: the unsaved body is never written to browser storage", !atRest.includes("민감한표식"));
  check("WP3c. M1: server draft still the last valid body after leaving", (await api.get(`/research/post-drafts/${validDraft}`)).textProjection === "kept-valid");
  // round4: 편집기를 떠난 글 목록에서도(편집기 unmount) 버퍼가 남아 있는 동안 새로고침·닫기 전에 확인한다
  await main.getByRole("link", { name: "← 내 글" }).click();
  await page.waitForURL(U("/write/"));
  await bodyBox().waitFor({ state: "detached" });
  check("WP3c. round4: on the post list (editor gone) leaving still asks while the buffer exists", await leaveGuarded());
  // 실제 새로고침에서 "머무르기"를 고르면 이동하지 않고 버퍼가 남는다
  const before = dialogsSeen.length;
  dismissNext = true;
  await page.reload({ timeout: 5000 }).catch(() => {});
  dismissNext = false;
  check("WP3c. round4: real reload shows the leave dialog", dialogsSeen.slice(before).includes("beforeunload"), JSON.stringify(dialogsSeen.slice(before)));
  await page.goBack();
  await page.waitForURL(/\/write\/new\/\?draft=/);
  check("WP3c. round4: after cancelling the leave, the unsaved body is still restored", await restoredOk());
  await writeBody(["kept-valid 고침"]);
  check("WP3c. M1: fixing the body lifts the block", (await reason.count()) === 0 && !(await main.getByRole("button", { name: "글 저장" }).isDisabled()));
  await main.getByRole("button", { name: "글 저장" }).click();
  await page.waitForURL(/\/write\/\?id=/);
  const fixed = await api.get(`/research/posts/${idParam()}`);
  check("WP3c. M1: saved version equals what the screen showed", fixed.latestVersion.textProjection === "kept-valid 고침", JSON.stringify(fixed.latestVersion.textProjection));
  check("WP3c. round4: after fixing and saving, leaving no longer asks", !(await leaveGuarded()));
}

// WP3d. round4: 저장 못 한 본문을 남긴 채 떠났다가 돌아와 초안을 버리면(명시적 폐기) 떠날 때 확인도 풀린다
{
  await page.goto(U("/write/new/"));
  await main.getByLabel("제목").fill("버릴 상한 초안");
  await writeBody(["discard-base"]);
  await saved();
  await page.keyboard.insertText("x".repeat(20001));
  await main.getByText("저장할 수 있는 크기·개수 상한을 넘었습니다", { exact: false }).waitFor();
  await main.getByRole("link", { name: "← 내 글" }).click();
  await page.waitForURL(U("/write/"));
  await page.goBack();
  await page.waitForURL(/\/write\/new\/\?draft=/);
  await main.getByText("저장할 수 있는 크기·개수 상한을 넘었습니다", { exact: false }).waitFor();
  await main.getByRole("button", { name: "초안 버리기…" }).click();
  await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
  await main.getByText("초안을 버렸습니다").waitFor();
  check("WP3d. round4: explicit discard releases the leave confirmation", !(await leaveGuarded()));
}

// WP3e. #36 리뷰 M1: 서버가 새 문서 계약을 요구해 저장을 거절한 동안의 입력(제목·본문·글 정보)은 앱 안 이동·뒤로가기 뒤에도
// 되살아나고, 새로고침·닫기는 확인으로 막는다. 서버가 다시 받으면 저장되고 확인이 풀린다. 새 글·기존 초안 모두.
{
  const upgrade = () => main.getByText("새 버전의 편집기가 있습니다", { exact: false });
  const notice = () => main.getByText("저장하지 못하고 떠났던 입력", { exact: false });
  const bodyText = async () => (await bodyBox().innerText()).replace(/\s+/g, " ").trim();
  const viaHome = async () => {
    const before = dialogsSeen.length;
    await page.locator("header").getByRole("link").first().click();
    await page.waitForURL(U("/"));
    const asked = dialogsSeen.length !== before;
    const guarded = await leaveGuarded();
    await page.goBack();
    await bodyBox().waitFor({ timeout: 15000 });
    return { asked, guarded };
  };
  await setMock("postRevision=1.2");
  try {
    // 새 글
    await page.goto(U("/write/new/"));
    await main.getByLabel("제목").fill("거절 제목");
    await writeBody(["거절 본문"]);
    await upgrade().waitFor({ timeout: 15000 });
    await main.getByLabel("제목").fill("거절 제목 고침");
    await main.getByLabel("태그", { exact: true }).fill("보존태그");
    await main.getByLabel("태그", { exact: true }).press("Enter");
    await bodyBox().click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" 추가");
    await page.waitForTimeout(300);
    const nav = await viaHome();
    check("WP3e. new post: in-app link leaves without a dialog; leaving the site from there still asks", !nav.asked && nav.guarded, JSON.stringify(nav));
    check(
      "WP3e. new post: title, body and tags are restored after Back, with a notice",
      (await main.getByLabel("제목").inputValue()) === "거절 제목 고침" && (await bodyText()) === "거절 본문 추가" && (await main.getByText("보존태그").count()) > 0 && (await notice().count()) === 1,
      `${await main.getByLabel("제목").inputValue()} | ${await bodyText()}`
    );
    await upgrade().waitFor({ timeout: 15000 });
    const before = dialogsSeen.length;
    dismissNext = true;
    await page.reload({ timeout: 5000 }).catch(() => {});
    dismissNext = false;
    check("WP3e. new post: cancelling a reload asks and keeps the input", dialogsSeen.slice(before).includes("beforeunload") && (await main.getByLabel("제목").inputValue()) === "거절 제목 고침");
    // 서버가 다시 받으면: 다시 열 때 되살린 입력이 저장되고 버퍼·떠날 때 확인이 풀린다
    await setMock("postRevision=1.1");
    await viaHome();
    await page.waitForURL(/[?&]draft=/, { timeout: 15000 }).catch(() => {});
    await saved();
    const made = await api.get(`/research/post-drafts/${await draftParam()}`);
    check("WP3e. new post: once accepted, the restored input is what the server saved", made.title === "거절 제목 고침" && made.textProjection === "거절 본문 추가" && made.tags.includes("보존태그"), JSON.stringify({ t: made.title, p: made.textProjection, tags: made.tags }));
    check("WP3e. new post: after saving, leaving no longer asks", !(await leaveGuarded()));
    await main.getByRole("button", { name: "초안 버리기…" }).click();
    await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
    await main.getByText("초안을 버렸습니다").waitFor();

    // 기존 초안
    const content = { format: "post-blocks", version: 1, blocks: [{ id: crypto.randomUUID(), type: "paragraph", runs: [{ text: "초안 기준", marks: [] }], align: "left" }] };
    const base = await api.send("POST", "/research/post-drafts", { title: "기존 초안", content, description: "", tags: [], coverImageId: null, documentIds: [] });
    check("WP3e. existing draft: setup", base.status === 201, String(base.status));
    await page.goto(U(`/write/new/?draft=${base.body.id}`));
    await bodyBox().waitFor({ timeout: 15000 });
    await setMock("postRevision=1.2");
    await main.getByLabel("제목").fill("기존 초안 고침");
    await bodyBox().click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" 거절됨");
    await upgrade().waitFor({ timeout: 15000 });
    const nav2 = await viaHome();
    check(
      "WP3e. existing draft: after an in-app link and Back, the rejected edits are restored over the saved draft",
      !nav2.asked && nav2.guarded && (await main.getByLabel("제목").inputValue()) === "기존 초안 고침" && (await bodyText()) === "초안 기준 거절됨",
      `${JSON.stringify(nav2)} ${await bodyText()}`
    );
    check("WP3e. existing draft: the server draft is unchanged while rejected", (await api.get(`/research/post-drafts/${base.body.id}`)).title === "기존 초안");
    await upgrade().waitFor({ timeout: 15000 });
    // 명시적으로 버리면 버퍼와 떠날 때 확인도 풀린다
    await main.getByRole("button", { name: "초안 버리기…" }).click();
    await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
    await main.getByText("초안을 버렸습니다").waitFor();
    check("WP3e. existing draft: discarding releases the leave confirmation", !(await leaveGuarded()));
  } finally {
    await setMock("postRevision=1.1");
  }
}

// WP3f. 리뷰 round2 M1: 되살린 입력을 다시 저장하는 요청이 끝나기 전에 또 떠나도 입력을 잃지 않는다.
// 그 요청이 409·네트워크 실패로 끝나면 버퍼(그 사이 고친 입력 포함)가 남아 다시 되살아나고, 늦게라도 성공하면 버퍼와 떠날 때 확인이 풀린다.
{
  const upgrade = () => main.getByText("새 버전의 편집기가 있습니다", { exact: false });
  const bodyText = async () => (await bodyBox().innerText()).replace(/\s+/g, " ").trim();
  const leave = async () => {
    await page.locator("header").getByRole("link").first().click();
    await page.waitForURL(U("/"));
  };
  const back = async () => {
    await page.goBack();
    await bodyBox().waitFor({ timeout: 15000 });
  };
  // 초안 저장(PUT)을 잡아 두었다가 원하는 결과로 돌려준다
  let held = [];
  // 네트워크 실패로 끝낸 요청은 닫힌 편집기가 같은 key로 다시 보낸다. 그 재시도는 바로 실패시켜 뒤 단계와 섞이지 않게 한다.
  const failedKeys = new Set();
  const hold = (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    if (failedKeys.has(route.request().headers()["idempotency-key"])) return route.abort("failed");
    held.push(route);
  };
  const waitHeld = async () => {
    for (let i = 0; i < 100 && !held.length; i++) await page.waitForTimeout(100);
    return held.length > 0;
  };
  const content = { format: "post-blocks", version: 1, blocks: [{ id: crypto.randomUUID(), type: "paragraph", runs: [{ text: "saved-base", marks: [] }], align: "left" }] };
  const base = await api.send("POST", "/research/post-drafts", { title: "느린 저장", content, description: "", tags: [], coverImageId: null, documentIds: [] });
  const draftUrl = /\/research\/post-drafts\/[^/]+$/;
  await page.goto(U(`/write/new/?draft=${base.body.id}`));
  await bodyBox().waitFor({ timeout: 15000 });
  await setMock("postRevision=1.2");
  try {
    await bodyBox().click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" UNSAVED-NEW-TEXT");
    await upgrade().waitFor({ timeout: 15000 });
    await leave();
    await setMock("postRevision=1.1");
    // (1) 되살린 입력의 저장을 잡아 둔 채 고치고 다시 떠난다 → 409로 끝낸다 → 돌아오면 고친 것까지 남아 있다
    await page.route(draftUrl, hold);
    await back();
    const sent = await waitHeld();
    await bodyBox().click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" MORE");
    await page.waitForTimeout(300);
    await leave();
    check("WP3f. leaving while the restored save is pending keeps the leave guard", sent && (await leaveGuarded()), `held=${held.length}`);
    for (const r of held.splice(0)) await r.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "post_contract_upgrade_required", message: "x", details: { requiredRevision: "1.2" } } }) });
    await page.waitForTimeout(500);
    check("WP3f. after that save is refused (409), the guard is still on", await leaveGuarded());
    await back();
    check("WP3f. back again: the input including edits made during the pending save is restored", (await bodyText()) === "saved-base UNSAVED-NEW-TEXT MORE", await bodyText());
    // (2) 같은 상황에서 네트워크 실패
    await waitHeld();
    await leave();
    for (const r of held.splice(0)) {
      failedKeys.add(r.request().headers()["idempotency-key"]);
      await r.abort("failed");
    }
    await page.waitForTimeout(500);
    check("WP3f. after a network failure of the pending save, the guard is still on", await leaveGuarded());
    await back();
    check("WP3f. back again after the network failure: input restored", (await bodyText()) === "saved-base UNSAVED-NEW-TEXT MORE", await bodyText());
    // (4) 리뷰 round3: 잡아 둔 저장(A) 중에 더 고치고 떠난다 → A는 늦게 성공, 이어 보낸 최신 저장(B)은 409 → 돌아오면 더 고친 입력까지 남는다
    await waitHeld();
    await bodyBox().click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" MORE2");
    await page.waitForTimeout(300);
    await leave();
    for (const r of held.splice(0)) await r.continue();
    const followUp = await waitHeld();
    for (const r of held.splice(0)) await r.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "post_contract_upgrade_required", message: "x", details: { requiredRevision: "1.2" } } }) });
    await page.waitForTimeout(500);
    const mid = await api.get(`/research/post-drafts/${base.body.id}`);
    check("WP3f. earlier save succeeded late, the follow-up save was refused, the guard is still on", followUp && mid.textProjection === "saved-base UNSAVED-NEW-TEXT MORE" && (await leaveGuarded()), `${followUp} ${mid.textProjection}`);
    await back();
    check("WP3f. back again: the newer input (after the late success) is restored, not dropped as another tab's", (await bodyText()) === "saved-base UNSAVED-NEW-TEXT MORE MORE2", await bodyText());
    // (3) 늦게 성공: 떠난 뒤 저장이 끝나면 버퍼와 확인이 풀리고, 서버 초안에 그 입력이 있다
    await waitHeld();
    await leave();
    for (const r of held.splice(0)) await r.continue();
    await page.waitForTimeout(1000);
    const after = await api.get(`/research/post-drafts/${base.body.id}`);
    check("WP3f. a late successful save stores the input and releases the guard", after.textProjection === "saved-base UNSAVED-NEW-TEXT MORE MORE2" && !(await leaveGuarded()), `${after.textProjection} guard=${await leaveGuarded()}`);
    await page.unroute(draftUrl, hold);
    await back();
    check("WP3f. re-opened after the late save: the server draft is shown without a stale restore", (await bodyText()) === "saved-base UNSAVED-NEW-TEXT MORE MORE2" && (await main.getByText("저장하지 못하고 떠났던 입력", { exact: false }).count()) === 0, await bodyText());
    await main.getByRole("button", { name: "초안 버리기…" }).click();
    await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
    await main.getByText("초안을 버렸습니다").waitFor();
  } finally {
    await page.unroute(draftUrl, hold).catch(() => {});
    await setMock("postRevision=1.1");
  }
}

// WP4. #31: 자료만 바탕으로 한 글, 보관 자료 제외, 문서 해제 뒤에도 자료 유지, 요약 없는 글의 목록 발췌(W2)
{
  const docB = (await api.send("POST", "/research/documents", { title: "바탕 문서 B", content: "문서 B" })).body;
  const matOld = await material("보관할 자료", "https://example.com/old", "보관 자료 본문");
  await relate(docB.id, matOld.id);
  await relate(docB.id, matA.id);
  await api.send("DELETE", `/research/materials/${matOld.id}`, { expectedVersion: matOld.version });
  await page.goto(U("/write/new/"));
  await main.getByLabel("제목").fill("자료만 쓴 글");
  await writeBody(["## 소제목", "**굵은** 첫 문장과 링크 그리고 `코드`.", "둘째 문단"]);
  await main.getByRole("button", { name: "문서 연결" }).click();
  await main.getByLabel("연결할 문서 찾기").fill("바탕 문서 B");
  await main.getByRole("button", { name: "바탕 문서 B 연결" }).click();
  const box = main.getByRole("region", { name: "바탕이 된 자료" });
  check("WP4. W3: archived material excluded on auto add", (await appears(box.getByText("보관한 자료 1개는 담지 않았습니다"))) && !(await box.innerText()).includes("보관할 자료"));
  check("WP4. W3: active material from document added", await appears(box.locator("li", { hasText: "문서 A의 자료" })));
  // 문서 연결을 해제해도 담긴 자료는 남고 출처만 직접 추가로 보인다
  await main.getByRole("region", { name: "연결 문서" }).getByRole("button", { name: "연결 해제" }).click();
  check("WP4. W3: unlinking a document keeps its material", await appears(box.locator("li", { hasText: "문서 A의 자료" }).getByText("직접 추가")));
  await saved();
  const d4 = await api.get(`/research/post-drafts/${await draftParam()}`);
  check("WP4. W3: materials-only draft", d4.documentIds.length === 0 && JSON.stringify(d4.materialIds) === JSON.stringify([matA.id]), JSON.stringify(d4));
  await main.getByRole("button", { name: "글 저장" }).click();
  await page.waitForURL(/\/write\/\?id=/);
  const p4 = await api.get(`/research/posts/${idParam()}`);
  check("WP4. W3: materials-only post saved", p4.latestVersion.documentIds.length === 0 && JSON.stringify(p4.latestVersion.materialIds) === JSON.stringify([matA.id]));
  const pub4 = main.getByRole("region", { name: "발행" });
  await pub4.getByRole("button", { name: "발행 미리보기" }).click();
  check("WP4. W3: preview lists public material title + URL", await appears(pub4.getByText("문서 A의 자료 — https://example.com/source-a")));
  await pub4.getByRole("button", { name: "이 버전 발행" }).click();
  await pub4.getByText("발행했습니다", { exact: false }).waitFor();
  const anon = await (await browser.newContext()).newPage();
  await anon.goto(U("/posts/"));
  const card = anon.locator("main article", { hasText: "자료만 쓴 글" });
  await card.waitFor();
  check("WP4. W2: card without summary shows plain body excerpt", await appears(card.getByText("소제목 굵은 첫 문장과 링크 그리고 코드. 둘째 문단")));
  const legacy = anon.locator("main article", { hasText: "Observatory에 오신 것을 환영합니다" });
  check("WP4. W2: existing summary still shown", await appears(legacy.getByText("스터디 그룹의 관찰과 기록을 위한 공간", { exact: false })));
  await anon.goto(U(`/posts/${p4.latestVersion.slug}/`));
  await anon.locator("article h1").first().waitFor();
  check("WP4. W2: article header has no summary line when empty", (await anon.locator("article header > p").count()) === 0);
  await anon.context().close();
}

// WP4b. 리뷰 F1: 자료 찾기 응답을 기다리는 동안(요청 2개가 겹쳐도) 사람이 뺀 자료는 늦은 응답이 다시 넣지 않는다.
//       뺀 뒤에 새로 연결한 문서는 그 자료를 다시 담을 수 있다.
{
  const doc = async (title) => (await api.send("POST", "/research/documents", { title, content: title })).body;
  const docC = await doc("경합 문서 C");
  const docD = await doc("경합 문서 D");
  const docE = await doc("경합 문서 E");
  for (const d of [docC, docD, docE]) await relate(d.id, matA.id);
  await page.goto(U("/write/new/"));
  await main.getByLabel("제목").fill("자료 제거 경합");
  await writeBody(["본문"]);
  const box = main.getByRole("region", { name: "바탕이 된 자료" });
  const row = box.locator("li", { hasText: "문서 A의 자료" });
  await box.getByRole("button", { name: "자료 추가" }).click();
  await box.getByLabel("추가할 자료 찾기").fill("문서 A의");
  await box.getByRole("button", { name: "문서 A의 자료 추가" }).click();
  await box.getByRole("button", { name: "찾기 닫기" }).click();
  await row.waitFor();
  let release;
  const held = new Promise((r) => (release = r));
  let holding = 0;
  const hold = async (route) => {
    holding++;
    await held;
    return route.continue().catch(() => {});
  };
  await page.route("**/api/research/post-material-options/resolve", hold);
  const link = async (title) => {
    await main.getByLabel("연결할 문서 찾기").fill(title);
    await main.getByRole("button", { name: `${title} 연결` }).click();
  };
  await main.getByRole("button", { name: "문서 연결" }).click();
  await link("경합 문서 C");
  await link("경합 문서 D");
  for (let i = 0; i < 50 && holding < 2; i++) await page.waitForTimeout(100);
  check("WP4b. setup: two resolve requests pending", holding === 2, String(holding));
  await row.getByRole("button", { name: "자료 빼기" }).click();
  check("WP4b. removed while resolving", (await row.count()) === 0);
  release();
  await main.getByText("연결한 문서의 자료를 찾는 중입니다").waitFor({ state: "detached" });
  await page.unroute("**/api/research/post-material-options/resolve", hold);
  check("WP4b. F1: late (overlapping) responses keep the removal", (await row.count()) === 0);
  let d = null;
  for (let i = 0; i < 30; i++) {
    const id = await draftParam();
    d = id ? await api.get(`/research/post-drafts/${id}`) : null;
    if (d && JSON.stringify(d.documentIds) === JSON.stringify([docC.id, docD.id])) break;
    await page.waitForTimeout(300);
  }
  check("WP4b. F1: saved draft keeps the removal", !!d && JSON.stringify(d.documentIds) === JSON.stringify([docC.id, docD.id]) && d.materialIds.length === 0, JSON.stringify(d && { documentIds: d.documentIds, materialIds: d.materialIds }));
  await link("경합 문서 E");
  check("WP4b. F1: a document linked after the removal adds it again", await appears(row.getByText("문서에서 자동 포함", { exact: false })));
  await main.getByRole("button", { name: "초안 버리기…" }).click();
  await main.getByRole("button", { name: "초안 버리기", exact: true }).click();
  await main.getByText("초안을 버렸습니다").waitFor();
}

// WP5. 기존 글 수정: 주소는 읽기 전용, 새 버전, 버전 비교에 글 정보 변경
await page.goto(U(`/write/?id=${postId}`));
await main.getByRole("button", { name: "내용 수정" }).click();
check("WP5. W1: public address read-only for saved post", (await main.getByLabel("공개 주소").inputValue()) === `/posts/${slug1}/` && (await main.getByLabel("공개 주소").getAttribute("readonly")) !== null);
// 직접 추가한 자료를 빼면 다음 버전에서 빠진다(자동 포함 자료도 같은 방식으로 뺄 수 있다)
await main.getByRole("region", { name: "바탕이 된 자료" }).locator("li", { hasText: "직접 고른 자료" }).getByRole("button", { name: "자료 빼기" }).click();
await writeBody(["# 첫 글 본문", "두 번째 버전 문장"]);
await main.getByLabel("태그", { exact: true }).fill("v2");
await main.getByLabel("태그", { exact: true }).press("Enter");
await saved();
await main.getByRole("button", { name: "새 버전으로 저장" }).click();
await main.getByRole("button", { name: "내용 수정" }).waitFor();
{
  const v2 = (await api.get(`/research/posts/${postId}`)).latestVersion;
  check("WP5. new version saved, slug kept, removed material gone", v2.number === 2 && v2.slug === slug1 && JSON.stringify(v2.materialIds) === JSON.stringify([matA.id]), JSON.stringify(v2));
}
await main.getByRole("button", { name: "버전 비교" }).click();
const cmp = main.getByRole("region", { name: "버전 비교" });
check("WP5. version compare shows body diff", await appears(cmp.locator('[data-diff="add"]', { hasText: "두 번째 버전 문장" })));
check("WP5. version compare shows meta diff (+tag)", await appears(cmp.getByLabel("글 정보 변경").getByText("+v2")));
check("WP5. version compare shows removed material", await appears(cmp.getByLabel("글 정보 변경").locator("del", { hasText: "직접 고른 자료" })));
await cmp.getByRole("button", { name: "비교 닫기" }).click();

// WP6. 발행: v1 미리보기 → 문서 제목 공개 안내 → 발행 → 비로그인 공개 화면
const pub = main.getByRole("region", { name: "발행" });
check("WP6. not published yet", await appears(pub.getByText("발행하지 않은 글입니다")));
const v1Id = p1.latestVersion.id;
await pub.getByLabel("발행할 버전").selectOption(v1Id);
await pub.getByRole("button", { name: "발행 미리보기" }).click();
check("WP6. preview warns linked document title becomes public", await appears(pub.getByText("함께 공개되는 문서 제목 (1)")));
check("WP6. preview marks private document as title only", await appears(pub.getByText(/바탕 문서 A — 제목만 표시/)));
check("WP6. W3: preview lists 2 public materials (title + URL only)", await appears(pub.getByText("함께 공개되는 자료 (2)")));
check("WP6. page preview renders the article", await appears(pub.locator("article h1", { hasText: "첫 글" })));
await pub.getByRole("button", { name: "이 버전 발행" }).click();
check("WP6. published note", await appears(pub.getByText("발행했습니다", { exact: false })));
check("WP6. status shows published v1 not latest", await appears(pub.getByText("최신 버전 v2은 아직 발행하지 않았습니다", { exact: false })));
{
  const anon = await (await browser.newContext()).newPage();
  await anon.goto(U(`/posts/${slug1}/`));
  await anon.locator("article h1").first().waitFor();
  const text = await anon.locator("main").innerText();
  check("WP6. public page shows v1 (not v2)", text.includes("첫 글 본문") && !text.includes("두 번째 버전 문장"));
  check(
    "WP6. W3: public page lists based-on materials (title + source URL, no body)",
    text.includes("바탕이 된 자료") &&
      text.includes("문서 A의 자료") &&
      text.includes("직접 고른 자료") &&
      (await anon.getByRole("link", { name: "https://example.com/source-b" }).count()) === 1 &&
      !text.includes("비밀 메모")
  );
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
await writeBody(["사람이 쓴 첫 문단"]);
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
await page.waitForURL(/\/write\/\?id=/);
check("WP9. accepted new post opens (no slug asked)", await appears(main.getByRole("heading", { name: "초안 글 (제안)" })));
check("WP9. W1: accept generated the slug", /^post-[0-9a-f]{32}$/.test((await api.get(`/research/posts/${idParam()}`)).latestVersion.slug));
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
await page.goto(U(`/posts/${slug1}/`));
await page.locator("article h1").first().waitFor();
await openChat("글 대화");
check("WP12. public post scope: bounded", await appears(chat.getByText("다음 질문 범위: 이 발행 글")));
{
  // 리뷰 F2: 공개 글의 postId를 확정하기 전·찾지 못한 경우에는 보내지 않는다(general/소유자 범위로 대신 보내지 않음).
  const sent = [];
  const record = (route) => {
    sent.push(route.request().postDataJSON());
    return route.continue().catch(() => {});
  };
  await page.route("**/api/research/sessions/*/messages", record);
  let release;
  const held = new Promise((r) => (release = r));
  const hold = async (route) => {
    await held;
    return route.continue().catch(() => {});
  };
  await page.route(`**/api/public/posts/${slug1}`, hold);
  await page.goto(U(`/posts/${slug1}/`));
  await openChat("글 대화");
  await chat.getByLabel("메시지").fill("느린 공개 글 질문");
  const sendBtn = chat.getByRole("button", { name: "보내기" });
  check("WP12. F2: send locked while public post loads", (await appears(chat.getByText("이 공개 글을 확인한 뒤에 질문할 수 있습니다", { exact: false }))) && (await sendBtn.isDisabled()));
  await chat.getByLabel("메시지").press("Enter").catch(() => {});
  await chat.locator("form").evaluate((f) => f.requestSubmit()).catch(() => {});
  await page.waitForTimeout(500);
  check("WP12. F2: nothing sent before scope is known", sent.length === 0, JSON.stringify(sent.map((b) => b.page)));
  // 조회가 끝나지 않으면 클라이언트 timeout(5초)으로 실패 화면이 된다. 실패해도 잠금은 유지된다.
  check("WP12. F2: load failure keeps send locked", (await appears(page.getByText("글을 불러오지 못했습니다", { exact: false }), 10000)) && (await sendBtn.isDisabled()));
  release();
  await page.unroute(`**/api/public/posts/${slug1}`, hold);
  await page.reload();
  await page.locator("article h1").first().waitFor();
  await chat.getByLabel("메시지").fill("느린 공개 글 질문");
  await sendBtn.and(page.locator(":enabled")).waitFor();
  await sendBtn.click();
  await chat.getByText("모의 응답: 느린 공개 글 질문").waitFor({ timeout: 15000 });
  check("WP12. F2: sent as public_post with postId after load", sent.at(-1)?.page?.kind === "public_post" && sent.at(-1)?.page?.resourceId === postId, JSON.stringify(sent.at(-1)?.page));
  // 찾을 수 없는 글: 계속 잠김
  await page.goto(U("/posts/no-such-post/"));
  await openChat("글 대화");
  await page.getByText("글을 찾을 수 없습니다").waitFor();
  await chat.getByLabel("메시지").fill("없는 글 질문");
  check("WP12. F2: not-found post keeps send locked", (await sendBtn.isDisabled()) && (await appears(chat.getByText("이 공개 글을 확인한 뒤에 질문할 수 있습니다", { exact: false }))));
  // 공개 글 간 이동: 이전 글의 postId를 다음 글에 쓰지 않는다
  let release2;
  const held2 = new Promise((r) => (release2 = r));
  const hold2 = async (route) => {
    await held2;
    return route.continue().catch(() => {});
  };
  await page.route("**/api/public/posts/welcome-to-observatory", hold2);
  await page.goto(U(`/posts/${slug1}/`));
  await page.locator("article h1").first().waitFor();
  await page.getByRole("link", { name: "글 목록" }).first().click();
  await page.getByRole("link", { name: /Observatory에 오신 것을 환영합니다/ }).first().click();
  await page.waitForURL(U("/posts/welcome-to-observatory/"));
  await openChat("글 대화");
  await chat.getByLabel("메시지").fill("다른 글 질문");
  check("WP12. F2: moving between posts relocks until the new post loads", await sendBtn.isDisabled());
  check("WP12. F2: nothing sent while moving", !sent.some((b) => b.text === "다른 글 질문"));
  release2();
  await page.unroute("**/api/public/posts/welcome-to-observatory", hold2);
  await page.unroute("**/api/research/sessions/*/messages", record);
  await chat.getByLabel("메시지").fill("");
}

// WP12b. 쓰는 이미지는 지울 수 없고, 쓰지 않는 이미지는 지울 수 있다
await page.goto(U(`/write/?id=${postId}`));
await main.getByRole("button", { name: "내용 수정" }).click();
const imgPanel = main.getByRole("region", { name: "이미지" });
// 기존 글 편집 중 업로드: 응답을 기다리는 동안 '편집 닫기'도 잠긴다(F1 terminal action).
let releaseSpare;
const heldSpare = new Promise((r) => (releaseSpare = r));
const holdSpare = async (route) => {
  if (route.request().method() === "POST") await heldSpare;
  return route.continue().catch(() => {});
};
await page.route("**/api/research/post-images", holdSpare);
await imgPanel.getByLabel("이미지 파일").setInputFiles({ name: "spare.webp", mimeType: "image/webp", buffer: Buffer.from("RIFF0000WEBPVP8 ") });
await main.getByText("이미지를 올리는 중입니다", { exact: false }).waitFor();
check(
  "WP12b. close/save locked while uploading in an existing post",
  (await main.getByRole("button", { name: "편집 닫기(초안 보관)" }).isDisabled()) && (await main.getByRole("button", { name: "새 버전으로 저장" }).isDisabled())
);
releaseSpare();
await imgPanel.getByText("올렸습니다", { exact: false }).waitFor();
await page.unroute("**/api/research/post-images", holdSpare);
check("WP12b. close unlocked after upload", !(await main.getByRole("button", { name: "편집 닫기(초안 보관)" }).isDisabled()));
// 방금 넣은 이미지 블록은 본문에서 다시 빼서 쓰지 않는 이미지로 만든다(본문을 새로 쓴다)
check("WP12b. spare image block inserted", (await bodyBox().locator("figure[data-type=post-image]").count()) === 1);
await writeBody(["두 번째 버전 문장"]);
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
  await anon.goto(U(`/posts/${slug1}/`));
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
