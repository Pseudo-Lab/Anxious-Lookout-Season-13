// #97 F5: 모의 cookie 관리 endpoint만 사용한다. 실제 PAT/연구 API/운영 자격은 발급하지 않는다.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (path) => `${ORIGIN}${BASE}${path}`;
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const main = page.locator("main");
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
await context.addInitScript(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { window.__patCopyCount = (window.__patCopyCount ?? 0) + 1; } } }));
const setMock = (query) => fetch(`${ORIGIN}/__mock/set?${query}`);
let records = [];
const attempts = new Map();
const posts = [];
let deletes = 0;
let lastSecret = "";
async function login(kind) {
  await setMock(`me=normal&health=normal&logout=normal&nextLogin=${kind}&reuse=0&as=&access=approved&codex=unavailable&runner=&runnerAll=`);
  await page.goto(U("/"));
  await page.locator("header").getByRole("link", { name: "GitHub 로그인" }).first().click();
  await page.locator("header").getByText("mock-user").first().waitFor();
}
const safe = (record) => ({ ...record });
await page.route(/\/api\/research\/pats(?:\/[^/?]+)?(?:\?.*)?$/, async (route) => {
  const request = route.request();
  assert.equal(request.headers().authorization, undefined);
  if (request.method() === "GET") return route.fulfill({ json: { items: records.map(safe), nextCursor: null } });
  assert.ok(request.headers()["x-csrf-token"]);
  if (request.method() === "DELETE") {
    deletes++;
    const record = records.find((item) => item.id === new URL(request.url()).pathname.split("/").at(-1));
    assert.ok(record);
    record.revokedAt = new Date().toISOString();
    return route.fulfill({ json: { credential: safe(record) } });
  }
  assert.equal(request.method(), "POST");
  const key = request.headers()["idempotency-key"];
  const body = request.postDataJSON();
  assert.deepEqual(Object.keys(body).sort(), ["expiryDays", "name"]);
  posts.push({ key, body });
  const previous = attempts.get(key);
  if (previous) {
    assert.deepEqual(body, previous.body);
    if (body.name === "Lost response" && previous.retries < 2) {
      const status = previous.retries++ === 0 ? 429 : 503;
      return route.fulfill({ status, json: { error: { code: status === 429 ? "rate_limited" : "not_ready" } } });
    }
    return route.fulfill({ status: 200, json: { credential: safe(previous.record), token: null, secretAvailable: false, replayed: true } });
  }
  const record = { id: crypto.randomUUID(), name: body.name, issuedAt: new Date().toISOString(), expiresAt: new Date(Date.now() + body.expiryDays * 86400000).toISOString(), revokedAt: null, lastUsedAt: null };
  records.unshift(record);
  attempts.set(key, { record, body, retries: 0 });
  lastSecret = `rpat1.${record.id}.${"a".repeat(43)}`;
  if (body.name === "Lost response") return route.abort(); // 서버에 해당하는 모의 상태만 적용, 응답 유실.
  return route.fulfill({ status: 201, json: { credential: safe(record), token: lastSecret, secretAvailable: true, replayed: false } });
});
const assertNoStoredSecret = async (value) => {
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.ok(!storage.includes(value));
  assert.ok(!page.url().includes(value));
};

try {
  await login("approved");
  // #119 D4: API 토큰은 계정 메뉴 안에 있다
  await page.locator("header").getByRole("button", { name: /계정 메뉴/ }).first().click();
  await page.locator("header").getByRole("link", { name: "API 토큰", exact: true }).first().click();
  await page.waitForURL(U("/account/tokens/"));
  await main.getByText("발급한 API 토큰이 없습니다.").waitFor();
  assert.equal(await main.getByLabel("API 주소", { exact: true }).inputValue(), `${ORIGIN}${BASE}/api/research/agent/v1`);
  assert.equal(await main.getByLabel("llm.md 주소", { exact: true }).inputValue(), `${ORIGIN}${BASE}/api/research/llm.md`);
  assert.equal(await main.getByLabel("권한", { exact: true }).count(), 0);
  await main.getByRole("button", { name: "API 주소 복사", exact: true }).click();
  await main.getByRole("button", { name: "llm.md 주소 복사", exact: true }).click();
  assert.equal(await page.evaluate(() => window.__patCopyCount), 2);
  await main.getByLabel("이름", { exact: true }).fill("  Desktop  ");
  await main.getByLabel("만료 기간(일)").fill("7");
  await main.getByRole("button", { name: "API 토큰 발급", exact: true }).click();
  const value = main.getByLabel("토큰 값", { exact: true });
  await value.waitFor();
  assert.equal(await value.inputValue(), lastSecret);
  const firstSecret = lastSecret;
  assert.deepEqual(posts[0].body, { name: "Desktop", expiryDays: 7 });
  await assertNoStoredSecret(firstSecret);
  await main.getByRole("button", { name: "토큰 복사", exact: true }).click();
  await main.getByRole("region", { name: "발급한 토큰 값" }).getByText("복사했습니다.", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.__patCopyCount), 3);
  await main.getByRole("button", { name: "확인했고 닫기" }).click();
  assert.equal(await value.count(), 0);
  assert.ok(!(await page.content()).includes(firstSecret));
  await page.reload();
  const list = main.getByRole("region", { name: "API 토큰 목록" });
  await list.getByText("Desktop", { exact: true }).waitFor();
  assert.ok(!(await page.content()).includes(firstSecret));
  await assertNoStoredSecret(firstSecret);
  records[0].lastUsedAt = new Date().toISOString();
  await main.getByRole("button", { name: "목록 새로 불러오기" }).click();
  await list.getByText("Desktop", { exact: true }).waitFor();
  assert.ok(!(await list.innerText()).includes("아직 사용하지 않음"));
  await list.getByRole("button", { name: "토큰 폐기", exact: true }).click();
  await list.getByRole("button", { name: "폐기 확인" }).click();
  await list.getByText("폐기됨", { exact: true }).waitFor();
  assert.equal(deletes, 1);
  console.log("PASS F5: first-only memory value/copy, no storage or URL, list has last use and immediate revoke; base-path downloads");

  await main.getByLabel("이름", { exact: true }).fill("Lost response");
  await main.getByRole("button", { name: "API 토큰 발급", exact: true }).click();
  await main.getByRole("button", { name: "같은 요청 다시 확인" }).waitFor();
  const lostPosts = posts.length;
  await page.waitForTimeout(300);
  assert.equal(posts.length, lostPosts);
  for (const status of [429, 503]) {
    await main.getByRole("button", { name: "같은 요청 다시 확인" }).click();
    if (status === 429) await main.getByText("요청이 많습니다.", { exact: false }).waitFor();
    else await main.getByText("요청 결과를 확인하지 못했습니다.", { exact: false }).waitFor();
    await main.getByRole("button", { name: "같은 요청 다시 확인" }).waitFor();
    assert.equal(await main.getByRole("button", { name: "API 토큰 발급", exact: true }).isDisabled(), true);
    assert.equal(posts.at(-1).key, posts[lostPosts - 1].key);
    assert.deepEqual(posts.at(-1).body, posts[lostPosts - 1].body);
    const count = posts.length;
    await page.waitForTimeout(300);
    assert.equal(posts.length, count);
  }
  await main.getByRole("button", { name: "같은 요청 다시 확인" }).click();
  await main.getByRole("button", { name: "값을 잃은 토큰 폐기" }).waitFor();
  assert.equal(posts.length, lostPosts + 3);
  assert.equal(posts.at(-1).key, posts.at(-2).key);
  assert.deepEqual(posts.at(-1).body, posts.at(-2).body);
  assert.equal(await value.count(), 0);
  assert.ok(!(await page.content()).includes(lastSecret));
  await main.getByRole("button", { name: "값을 잃은 토큰 폐기" }).click();
  await main.getByText("토큰을 폐기했습니다.", { exact: true }).waitFor();
  assert.equal(posts.length, lostPosts + 3);
  console.log("PASS F5: ACK loss then 429/503 keeps original unknown attempt and locks fresh issue; same-key replay has no value and revoke creates no replacement");

  { const m = page.locator("header").getByRole("button", { name: /계정 메뉴/ }).first(); if ((await m.getAttribute("aria-expanded")) !== "true") await m.click(); }
  await page.locator("header").getByRole("button", { name: "로그아웃", exact: true }).first().click();
  await page.locator("header").getByRole("link", { name: "GitHub 로그인", exact: true }).first().waitFor();
  records = [];
  await login("approved-commenter");
  await page.goto(U("/account/tokens/"));
  await main.getByText("현재 계정은 공개 콘텐츠만 읽을 수 있습니다.", { exact: true }).waitFor();
  await main.getByLabel("이름", { exact: true }).fill("Commenter");
  await main.getByRole("button", { name: "API 토큰 발급", exact: true }).click();
  await value.waitFor();
  // bfcache 보존 경로도 pagehide 순간에 표시 값을 지워야 한다.
  const commenterSecret = lastSecret;
  await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
  assert.equal(await value.count(), 0);
  assert.ok(!(await page.content()).includes(commenterSecret));
  await list.getByText("Commenter", { exact: true }).waitFor();
  assert.ok(!(await list.innerText()).includes("권한"));
  await page.route(U("/api/auth/session"), async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ json: { ...body, user: { ...body.user, isApproved: false } } });
  });
  await page.reload();
  await main.getByText("승인 후 발급할 수 있습니다.", { exact: false }).waitFor();
  assert.equal(await main.getByRole("button", { name: "API 토큰 발급", exact: true }).isDisabled(), true);
  await list.getByText("Commenter", { exact: true }).waitFor();
  await list.getByRole("button", { name: "토큰 폐기", exact: true }).click();
  await list.getByRole("button", { name: "폐기 확인" }).click();
  await list.getByText("폐기됨", { exact: true }).waitFor();
  console.log("PASS F5: commenter issue with effective public scope, approval loss disables issue but preserves list/revoke");
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
