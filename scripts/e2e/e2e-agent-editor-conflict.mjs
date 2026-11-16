// #97 F4: 다른 작성자가 편집 중 새 버전을 저장해도 입력·서버 버전을 보존하고 비교 뒤에만 저장한다.
// review가 모의 API 뒤에서 실행한다. 실제 Codex의 저장 경로·작성자 검증은 별도 개발 VM gate다.
import assert from "node:assert/strict";
import { chromium } from "playwright";

// run.sh는 선택한 파일만 Playwright의 /tmp/t로 복사하므로 상대 모듈 import 없이 입력을 만든다.
const bodyOf = (text) => ({ format: "post-blocks", version: 1, blocks: [{ id: crypto.randomUUID(), type: "paragraph", runs: [{ text, marks: [] }], align: "left" }] });

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (path) => `${ORIGIN}${BASE}${path}`;
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const main = page.locator("main");
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));

async function get(path) {
  const response = await page.request.get(U(`/api/research${path}`));
  assert.equal(response.status(), 200);
  return response.json();
}
async function send(method, path, data) {
  const me = await (await page.request.get(U("/api/auth/me"))).json();
  const response = await page.request.fetch(U(`/api/research${path}`), {
    method, data,
    headers: {
      Origin: ORIGIN,
      "X-CSRF-Token": me.csrfToken,
      "Idempotency-Key": crypto.randomUUID(),
      "X-Post-Contract-Revision": "1.1",
    },
  });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}

try {
  await fetch(`${ORIGIN}/__mock/set?me=normal&health=normal&logout=normal&nextLogin=approved&access=approved&codex=unavailable`);
  await page.goto(U("/"));
  await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().click();
  await page.locator("header").getByText("mock-user").first().waitFor();

  // 문서: 내 초안이 이미 자동 저장된 뒤 다른 작성자가 새 버전을 만든 경우(commit 409).
  const doc = await send("POST", "/documents", { title: "충돌 확인 문서", content: "첫 버전" });
  await page.goto(U(`/research/document/?id=${doc.id}`));
  await main.getByRole("button", { name: "내용 수정" }).click();
  await main.getByLabel("본문").fill("보존할 내 문서 입력");
  await main.getByText("초안 자동 저장됨").waitFor();
  const newer = await send("PATCH", `/documents/${doc.id}`, { expectedVersion: doc.version, title: doc.title, content: "다른 작성자가 저장한 문서" });
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  await main.getByRole("button", { name: "비교했고 이 내용으로 저장" }).waitFor();
  assert.equal(await main.getByLabel("본문").inputValue(), "보존할 내 문서 입력");
  assert.equal((await get(`/documents/${doc.id}`)).latestVersion.id, newer.latestVersion.id);
  await main.locator('[data-diff="del"]', { hasText: "다른 작성자가 저장한 문서" }).waitFor();
  await main.locator('[data-diff="add"]', { hasText: "보존할 내 문서 입력" }).waitFor();
  await main.getByRole("button", { name: "비교했고 이 내용으로 저장" }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  const savedDoc = await get(`/documents/${doc.id}`);
  assert.equal(savedDoc.content, "보존할 내 문서 입력");
  assert.equal(savedDoc.latestVersion.number, newer.latestVersion.number + 1);
  assert.equal((await get(`/documents/${doc.id}/versions/${newer.latestVersion.id}`)).content, "다른 작성자가 저장한 문서");
  console.log("PASS #97 F4 document: commit conflict preserves both versions, shows diff, saves only after confirmation");

  // 글: 편집기를 연 뒤 첫 초안을 만들기 전에 다른 작성자가 새 버전을 만든 경우(create 409).
  const text = { title: "충돌 확인 글", content: bodyOf("첫 글 본문"), description: "", tags: [], coverImageId: null, documentIds: [], materialIds: [] };
  const draft = await send("POST", "/post-drafts", text);
  const initial = (await send("POST", `/post-drafts/${draft.id}/commit`, { expectedDraftRevision: draft.revision })).post;
  await page.goto(U(`/write/?id=${initial.id}`));
  await main.getByRole("button", { name: "내용 수정" }).click();
  const body = main.locator(".ProseMirror");
  await body.waitFor();
  const otherDraft = await send("POST", "/post-drafts", {
    ...text, content: bodyOf("다른 작성자가 저장한 글"), postId: initial.id,
    basePostVersion: initial.version, baseVersionId: initial.latestVersion.id,
  });
  const other = (await send("POST", `/post-drafts/${otherDraft.id}/commit`, { expectedDraftRevision: otherDraft.revision, expectedVersion: initial.version })).post;
  await body.click();
  await page.keyboard.press("Control+a");
  await page.keyboard.type("보존할 내 글 입력");
  await main.getByText("자동 저장을 멈췄습니다. 글이 그사이 바뀌었습니다", { exact: false }).waitFor();
  assert.equal((await get(`/posts/${initial.id}`)).latestVersion.id, other.latestVersion.id);

  // 다시 조회가 실패해도 편집기를 닫거나 입력을 지우지 않는다.
  const endpoint = `**/api/research/posts/${initial.id}`;
  const failGet = (route) => route.request().method() === "GET" ? route.abort() : route.continue();
  await page.route(endpoint, failGet);
  await main.getByRole("button", { name: "최신 글을 불러와 계속" }).click();
  await main.getByText("최신 내용을 불러오지 못했습니다", { exact: false }).waitFor();
  assert.equal(await body.innerText(), "보존할 내 글 입력");
  await page.unroute(endpoint, failGet);
  await main.getByRole("button", { name: "최신 글을 불러와 계속" }).click();
  await main.getByText("초안 자동 저장됨").waitFor();
  await main.getByRole("button", { name: "새 버전으로 저장" }).click();
  await main.getByRole("button", { name: "비교했고 이 내용으로 저장" }).waitFor();
  await main.locator('[data-diff="del"]', { hasText: "다른 작성자가 저장한 글" }).waitFor();
  await main.locator('[data-diff="add"]', { hasText: "보존할 내 글 입력" }).waitFor();
  assert.equal((await get(`/posts/${initial.id}`)).latestVersion.id, other.latestVersion.id);
  await main.getByRole("button", { name: "비교했고 이 내용으로 저장" }).click();
  await main.getByRole("button", { name: "내용 수정" }).waitFor();
  const savedPost = await get(`/posts/${initial.id}`);
  assert.equal(savedPost.latestVersion.number, other.latestVersion.number + 1);
  assert.equal(savedPost.latestVersion.textProjection.trim(), "보존할 내 글 입력");
  assert.equal((await get(`/posts/${initial.id}/versions/${other.latestVersion.id}`)).textProjection.trim(), "다른 작성자가 저장한 글");
  console.log("PASS #97 F4 post: stale base and failed reload preserve input, diff precedes save, previous version remains");
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
