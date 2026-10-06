import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  const apiRequests = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.includes('/api/')) apiRequests.push(request.url());
  });
  await page.goto('http://127.0.0.1:8080/Anxious-Lookout-Season-13/auth/login/');
  await page.getByText('이 정적 사이트에는 API 서버가 없어 로그인을 제공하지 않습니다.').waitFor();
  assert.equal(await page.getByRole('link', {name: 'GitHub로 계속하기'}).count(), 0);
  await page.goto('http://127.0.0.1:8080/Anxious-Lookout-Season-13/status/');
  await page.getByText('웹과 API 서버의 연결, 배포 버전, 로그인 상태를 확인합니다.').waitFor();
  assert.deepEqual(apiRequests, []);
  console.log('PASS Pages export browser with prefix: login disabled and zero API requests');
} finally {
  await browser.close();
}
