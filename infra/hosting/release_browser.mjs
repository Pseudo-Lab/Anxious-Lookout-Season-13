// Read-only candidate UI check: no provider/cookie/identity writes.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const [sha, builtAt] = process.argv.slice(2);
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  assert.equal((await page.goto('http://127.0.0.1:8080/')).status(),200);
  await page.goto('http://127.0.0.1:8080/auth/login/');
  await page.getByRole('link',{name:'GitHub로 계속하기'}).waitFor();
  for (const path of ['/api/version','/version.json']) {
    const value = await page.evaluate(async path => (await fetch(path)).json(),path);
    assert.deepEqual(value,{sha,builtAt});
  }
  assert.equal((await page.goto('http://127.0.0.1:8080/no-release-browser-page/',{waitUntil:'networkidle'})).status(),404);
  assert.deepEqual(errors,[]);
  console.log('PASS read-only Chromium candidate root/login/error/assets and exact web/API metadata');
} finally {
  await browser.close();
}
