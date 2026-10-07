import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  const unexpected = [];
  // Fresh disposable provider identity makes replay independent of the approved
  // account intentionally created by the DB restart/restore seed fixture.
  const providerId = String(Date.now());
  await page.route('**/api/auth/github/start', async route => {
    const response = await route.fetch({maxRedirects: 0});
    assert.equal(response.status(), 302);
    const url = new URL(response.headers().location);
    assert.equal(url.hostname, 'mock-github');
    url.searchParams.set('github_id', providerId);
    await route.fulfill({response, headers: {...response.headers(), location: url.toString()}});
  });
  page.on('pageerror', error => unexpected.push(error.message));
  await page.goto('http://127.0.0.1:8080/auth/login/');
  await page.getByRole('link', {name: 'GitHub로 계속하기'}).waitFor();
  await page.getByRole('link', {name: 'GitHub로 계속하기'}).click();
  await page.waitForURL('http://127.0.0.1:8080/');
  await page.getByRole('button', {name: '로그아웃'}).waitFor();
  const identity = await page.evaluate(async () => (await fetch('/api/auth/me')).json());
  assert.equal(identity.user.login, 'mock-user');
  assert.equal(identity.user.isApproved, false);
  assert.notEqual(identity.user.accountId, identity.user.githubId);
  assert.equal(await page.evaluate(() => document.cookie.includes('anxious_session')), false);
  const sessionCookie = (await page.context().cookies('http://127.0.0.1:8080/api/auth/me')).find(cookie => cookie.name === 'anxious_session');
  assert.equal(sessionCookie.httpOnly, true);
  assert.equal(sessionCookie.sameSite, 'Lax');
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  await page.goto('http://127.0.0.1:8080/auth/login/');
  await page.getByText('관리자 승인을 기다리고 있습니다.').waitFor();
  const logoutResponse = page.waitForResponse(response => response.url().endsWith('/api/auth/logout') && response.request().method() === 'POST');
  await page.getByRole('button', {name: '로그아웃'}).click();
  assert.equal((await logoutResponse).status(), 204);
  await page.getByRole('link', {name: 'GitHub 로그인', exact: true}).waitFor();
  assert.equal(await page.evaluate(async () => (await fetch('/api/auth/me')).status), 401);
  assert.deepEqual(unexpected, []);
  console.log('PASS Chromium actual Next/Traefik/FastAPI/PG with mock OAuth: pending identity, HttpOnly session, CSRF logout, guest recovery');
} finally {
  await browser.close();
}
