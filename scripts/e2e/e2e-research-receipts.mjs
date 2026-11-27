// #97 r2 F2 모의 표시 검증. endpoint 응답 주입이며 실제 CLI/API receipt 작성·VM 검증을 대체하지 않는다.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (path) => `${ORIGIN}${BASE}${path}`;
const browser = await chromium.launch();
const page = await browser.newPage();
const chat = page.locator("#chat-panel");
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const id = () => crypto.randomUUID();
let mutations = 0;
page.on("request", (request) => { if (request.method() !== "GET" && new URL(request.url()).pathname.includes("/api/research/")) mutations++; });

try {
  await fetch(`${ORIGIN}/__mock/set?me=normal&health=normal&logout=normal&nextLogin=approved&access=approved&codex=fixture&stream=off&window=&remaining=`);
  await page.goto(U("/"));
  await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().click();
  await page.locator("header").getByText("mock-user").first().waitFor();
  const me = await (await page.request.get(U("/api/auth/me"))).json();
  const response = await page.request.post(U("/api/research/sessions"), {
    headers: { Origin: ORIGIN, "X-CSRF-Token": me.csrfToken, "Idempotency-Key": id() }, data: { title: "작업 기록 검증" },
  });
  assert.equal(response.status(), 201);
  const session = await response.json();
  const requestId = id();
  const resourceId = id();
  const versionId = id();
  const saved = { resourceType: "document", id: resourceId, title: "<img src=x onerror=alert(1)> 기록", versionId, versionNumber: 2, destination: { path: "/research/document/", resourceId, versionId } };
  const first = { id: id(), requestId, operation: "research_document_save", status: "succeeded", at: "2026-11-17T01:00:00Z", saved };
  const failed = { id: id(), requestId, operation: "research_get", status: "failed", at: first.at, code: "private-native-reason" };
  const read = { id: id(), requestId, operation: "research_page_context", status: "succeeded", at: first.at, count: 0, snapshot: { type: "public_document", resourceId, versionId } };
  let records = [];
  let modelState = "failed";
  let receiptStatus = 200;
  let reads = 0;
  let holdNext = false;
  let releaseFirst;
  const endpoint = U(`/api/research/sessions/${session.id}/research-calls`);
  await page.route(endpoint, async (route) => {
    assert.equal(route.request().method(), "GET");
    assert.equal(route.request().headers().authorization, undefined);
    reads++;
    if (holdNext) {
      holdNext = false;
      await new Promise((resolve) => { releaseFirst = resolve; });
      return route.fulfill({ json: { items: [] } });
    }
    return route.fulfill({ status: receiptStatus, json: receiptStatus === 200 ? { items: records } : { error: { code: "not_ready" } } });
  });
  await page.route(U(`/api/research/sessions/${session.id}`), async (route) => {
    const original = await route.fetch();
    const body = await original.json();
    await route.fulfill({ json: {
      ...body, state: modelState, error: modelState === "failed" ? { code: "codex_rejected", message: "fixed fixture" } : null,
      items: [
        { id: "model", type: "message", role: "assistant", text: JSON.stringify({ saved }) },
        { id: "shell", type: "tool_call", name: "shell", status: "completed", input: { command: "fixture output", cwd: "." }, output: { stdout: JSON.stringify({ saved }), stderr: null } },
        { id: "old-dynamic", type: "tool_call", name: "research_document_save", status: "completed", input: null, output: { saved } },
        { id: "empty", type: "tool_call", name: "", status: "completed", input: null, output: null },
      ],
    } });
  });

  const initialReceipt = page.waitForResponse(endpoint);
  await page.goto(U(`/research/session/?id=${session.id}`));
  await chat.getByLabel("메시지").waitFor();
  await chat.getByText("마지막 요청이 실패했습니다.", { exact: false }).waitFor();
  await initialReceipt;
  assert.equal(await chat.getByRole("region", { name: "연구 작업 기록" }).count(), 0);
  assert.equal(await chat.getByRole("list", { name: "저장한 문서·글" }).count(), 0);
  console.log("PASS F2: shell/model/old dynamic saved JSON does not create an activity row");
  await chat.getByRole("button", { name: "도구 실행함", exact: true }).last().click();
  await chat.getByText("기록된 입력이 없습니다.").waitFor();
  await chat.getByText("기록된 출력이 없습니다.").waitFor();

  // 초기 receipt GET을 늦춰 새 session poll 뒤의 최신 receipt 응답을 덮어쓰는 경합을 재현한다.
  records = [first, first, { ...first, id: id() }, failed, read];
  modelState = "running";
  holdNext = true;
  const countBefore = reads;
  await page.reload();
  const activity = chat.getByRole("list", { name: "저장한 문서·글" });
  await activity.waitFor({ timeout: 15000 });
  assert.ok(reads >= countBefore + 2);
  assert.equal(await activity.locator("[data-research-call]").count(), 1);
  assert.equal(await chat.getByRole("region", { name: "연구 작업 기록" }).count(), 0);
  const delayed = page.waitForResponse(endpoint);
  releaseFirst();
  await (await delayed).finished();
  await page.waitForTimeout(150);
  assert.equal(await activity.locator("[data-research-call]").count(), 1);
  assert.equal(await activity.locator("img").count(), 0);
  assert.ok((await activity.innerText()).includes(saved.title));
  assert.equal(await activity.getByRole("link", { name: "보기", exact: true }).getAttribute("href"), `${BASE}/research/document/${resourceId}/#version-history`);
  assert.ok(!(await activity.innerText()).includes("private-native-reason"));
  console.log("PASS F2: latest authoritative receipts survive slow initial GET; replay groups once, plain title and safe link");

  const writesBefore = mutations;
  receiptStatus = 503;
  await chat.getByText("저장 기록 확인 실패", { exact: true }).waitFor({ timeout: 15000 });
  assert.equal(await activity.locator("[data-research-call]").count(), 1);
  receiptStatus = 200;
  modelState = "failed";
  await chat.getByRole("button", { name: "다시 확인", exact: true }).click();
  await chat.getByText("저장 기록 확인 실패", { exact: true }).waitFor({ state: "detached" });
  await chat.getByText("마지막 요청이 실패했습니다.", { exact: false }).waitFor({ timeout: 15000 });
  assert.equal(mutations, writesBefore);
  assert.equal(await activity.locator("[data-research-call]").count(), 1);
  await page.reload();
  await activity.waitFor();
  assert.equal(await activity.locator("[data-research-call]").count(), 1);
  await chat.getByText("마지막 요청이 실패했습니다.", { exact: false }).waitFor();
  console.log("PASS F2: fetch failure keeps confirmed receipts, GET-only retry, reload persistence and failed model outcome remain separate");
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
