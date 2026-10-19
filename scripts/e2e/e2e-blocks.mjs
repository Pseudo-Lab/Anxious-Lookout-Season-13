// #32 spike: 고정 블록 편집기(Tiptap v3) 시험 화면(/write/blocks-lab/)을 실제 브라우저로 확인한다.
// - K*: 한국어 IME. Chromium CDP(Input.imeSetComposition/insertText)로 자모 단위 조합을 흉내 낸다.
//   OS IME 자체를 쓰는 것이 아니므로 실제 기기(macOS Safari·iOS·Android) 수동 확인을 대신하지 않는다.
// - S*: 계약 구조 제한(목록 3단·표 병합 거절·columns·mark 공존·링크 정책·id 유일성·붙여넣기 정리).
// - Y*: YouTube 카드. 편집 화면에서 YouTube 관련 외부 요청이 하나도 없어야 한다.
// - I*: 이미지 붙여넣기·파일 선택 업로드(모의 API).
// - W*: WebKit·Firefox 기본 편집(조합 없는 입력만 가능).
import { chromium, firefox, webkit } from "playwright";

const ORIGIN = process.env.ORIGIN ?? "http://127.0.0.1:8080";
const BASE = process.env.BASE_PATH ?? "";
const U = (p) => `${ORIGIN}${BASE}${p}`;
const LAB = U("/write/blocks-lab/");

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}
const setMock = (q) => fetch(`${ORIGIN}/__mock/set?${q}`).then((r) => r.json());
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
const YT_HOSTS = /(^|\.)(youtube\.com|youtube-nocookie\.com|youtu\.be|ytimg\.com|googlevideo\.com|ggpht\.com)$/;

// ---- 문서 JSON 도우미 ----
function walk(node, fn, parents = []) {
  fn(node, parents);
  for (const c of node.content ?? []) walk(c, fn, [...parents, node]);
}
const textOf = (node) => (node.content ?? []).map((c) => (c.type === "text" ? c.text : c.type === "hardBreak" ? "\n" : textOf(c))).join("");
const blocks = (doc) => (doc?.content ?? []).filter((b) => !(b.type === "paragraph" && !b.content));
function listDepth(node, d = 0) {
  let max = d;
  for (const c of node.content ?? []) max = Math.max(max, listDepth(c, ["bulletList", "orderedList"].includes(c.type) ? d + 1 : d));
  return max;
}

// ---- 한글 자모 분해: 2벌식처럼 초성 → 초성+중성 → 완성 순으로 조합 문자열을 만든다 ----
const CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
function steps(ch) {
  const c = ch.charCodeAt(0) - 0xac00;
  if (c < 0 || c > 11171) return [ch];
  const l = Math.floor(c / 588);
  const v = Math.floor((c % 588) / 28);
  const lv = String.fromCharCode(0xac00 + l * 588 + v * 28);
  return c % 28 ? [CHO[l], lv, ch] : [CHO[l], lv];
}

await setMock("me=normal&health=normal&logout=normal&nextLogin=approved&access=approved&codex=fixture&window=&remaining=&runner=&runnerAll=");

// 모의 OAuth 시작 주소로 바로 들어가 session을 받는다(로그인 버튼 흐름은 e2e.mjs가 확인한다)
async function login(page) {
  await page.goto(U("/api/auth/github/start"), { waitUntil: "domcontentloaded" });
  await page.locator("header").getByText("mock-user").first().waitFor();
}

function harness(page) {
  const main = page.locator("main");
  const editor = main.locator(".ProseMirror");
  const pre = main.locator('pre[aria-label="편집기 JSON"]');
  const tb = main.getByRole("toolbar", { name: "서식" });
  return {
    main,
    editor,
    tb,
    async fresh() {
      // load는 전역 폰트 CDN 응답에 묶여 있어 기다리지 않는다(편집기 표시를 기다린다)
      await page.goto(LAB, { waitUntil: "domcontentloaded" });
      await editor.waitFor({ timeout: 15000 });
      await editor.click();
    },
    async doc() {
      await page.waitForTimeout(80);
      const t = await pre.textContent();
      return t ? JSON.parse(t) : null;
    },
    async blocks() {
      await page.waitForTimeout(80);
      const status = await main.getByRole("status", { name: "계약 검사" }).innerText();
      const t = await main.locator('pre[aria-label="post-blocks JSON"]').textContent();
      return { status, doc: t ? JSON.parse(t) : null };
    },
    notice: () => main.getByRole("status").filter({ hasText: /./ }).allInnerTexts(),
  };
}

// ======================= Chromium =======================
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const pageErrors = [];
const external = [];
page.on("pageerror", (e) => pageErrors.push(e.message));
page.on("request", (r) => {
  const host = new URL(r.url()).hostname;
  if (YT_HOSTS.test(host)) external.push(r.url());
});
let nextPrompt = [];
page.on("dialog", (d) => {
  if (d.type() === "prompt") void d.accept(nextPrompt.shift() ?? "");
  else void d.accept();
});
const H = harness(page);
const cdp = await context.newCDPSession(page);
const comp = (text) => cdp.send("Input.imeSetComposition", { text, selectionStart: text.length, selectionEnd: text.length });
const commit = (text) => cdp.send("Input.insertText", { text });
/**
 * 조합 중에 Enter·Tab 같은 키를 누를 때 OS IME가 보내는 순서를 흉내 낸다:
 * keydown(keyCode 229, isComposing) → 조합 확정(compositionend) → 실제 키(keydown 13 등).
 * CDP로 키만 보내면 조합이 확정되지 않은 채 남는데, 실제 IME에서는 생기지 않는 상태라 쓰지 않는다.
 */
async function imeKey(composing, key) {
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Process", code: key, windowsVirtualKeyCode: 229, nativeVirtualKeyCode: 229 });
  await commit(composing);
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Process", code: key, windowsVirtualKeyCode: 229, nativeVirtualKeyCode: 229 });
  await page.keyboard.press(key);
}
/** 단어를 자모 단위로 조합한다. leaveLast면 마지막 음절을 조합 중인 채로 둔다. */
async function typeKo(word, { leaveLast = false } = {}) {
  const chars = [...word];
  for (const [i, ch] of chars.entries()) {
    if (ch === " ") {
      await commit(" ");
      continue;
    }
    for (const s of steps(ch)) await comp(s);
    if (!(leaveLast && i === chars.length - 1)) await commit(ch);
  }
}

await login(page);
await page.goto(LAB);
check("L0. lab page renders the editor and toolbar", await H.editor.isVisible().catch(() => false) || (await H.editor.waitFor({ timeout: 15000 }).then(() => true, () => false)));

// K1. 기본 조합
await H.fresh();
await typeKo("안녕하세요 반갑습니다");
let d = await H.doc();
check("K1. composed Korean text is kept exactly", textOf(blocks(d)[0] ?? {}) === "안녕하세요 반갑습니다", JSON.stringify(textOf(blocks(d)[0] ?? {})));

// K2. 받침 이동(안 → 아+니): 조합 중이던 음절이 바뀌어 확정되고 다음 음절 조합이 시작된다
await H.fresh();
for (const s of ["ㅇ", "아", "안"]) await comp(s);
await commit("아");
for (const s of ["ㄴ", "니"]) await comp(s);
await commit("니");
d = await H.doc();
check("K2. final consonant moving to next syllable (안→아니)", textOf(blocks(d)[0] ?? {}) === "아니", JSON.stringify(textOf(blocks(d)[0] ?? {})));

// K3a. 조합 확정 직후 Enter(Chrome 순서: compositionend → keydown Enter)
await H.fresh();
await typeKo("첫줄");
await page.keyboard.press("Enter");
await typeKo("둘째");
d = await H.doc();
check("K3a. Enter right after commit splits paragraph without losing the last syllable", blocks(d).map(textOf).join("|") === "첫줄|둘째", blocks(d).map(textOf).join("|"));

// K3b. 조합 중 Enter(tiptap#4108 경로): 마지막 음절이 조합 중일 때 Enter
await H.fresh();
await typeKo("한글", { leaveLast: true });
await imeKey("글", "Enter");
await typeKo("다음");
d = await H.doc();
const k3b = blocks(d).map(textOf).join("|");
check("K3b. Enter while composing keeps the composing syllable (tiptap#4108 path)", k3b === "한글|다음", JSON.stringify(k3b));

// K4. 조합 중 Backspace(IME 안에서 자모 삭제) → 조합 취소, 확정 뒤 Backspace는 음절 단위
await H.fresh();
await typeKo("가나");
for (const s of ["ㄷ", "다", "닭"]) await comp(s);
for (const s of ["달", "다", "ㄷ"]) await comp(s);
await comp("");
d = await H.doc();
const k4a = textOf(blocks(d)[0] ?? {});
await page.keyboard.press("Backspace");
d = await H.doc();
check("K4. jamo backspace inside composition and cancel leave committed text intact; Backspace removes one syllable", k4a === "가나" && textOf(blocks(d)[0] ?? {}) === "가", `${k4a} → ${textOf(blocks(d)[0] ?? {})}`);

// K5. 줄 처음 stored mark(prosemirror#1458): 빈 문단에서 굵게 켜고 조합
await H.fresh();
await page.keyboard.press("Control+b");
await typeKo("굵게");
d = await H.doc();
const k5 = blocks(d)[0]?.content ?? [];
check("K5. stored bold mark at line start applies to composed text, no duplication", k5.length === 1 && k5[0].text === "굵게" && k5[0].marks?.some((m) => m.type === "bold"), JSON.stringify(k5));

// K6. mark 경계: 굵게 끄고 이어서 조합
await page.keyboard.press("Control+b");
await typeKo("보통");
d = await H.doc();
const k6 = blocks(d)[0]?.content ?? [];
check("K6. mark boundary: bold off then composing creates a plain run", k6.length === 2 && k6[1].text === "보통" && !(k6[1].marks ?? []).length, JSON.stringify(k6));

// K7. 목록: "- " 입력 규칙, 조합, Enter로 다음 항목, Tab 들여쓰기
await H.fresh();
await page.keyboard.type("- ");
await typeKo("항목");
await page.keyboard.press("Enter");
await page.keyboard.press("Tab");
await typeKo("하위", { leaveLast: true });
await imeKey("위", "Enter");
await typeKo("셋째");
d = await H.doc();
const list = blocks(d)[0];
const items = [];
walk(list ?? {}, (n) => n.type === "listItem" && items.push(textOf(n.content?.[0] ?? {})));
check("K7. list input rule + composing + Enter + Tab nesting", list?.type === "bulletList" && listDepth(d) === 2 && items.join("|") === "항목|하위|셋째", `${list?.type} depth=${listDepth(d)} items=${items.join("|")}`);

// K8. 표 칸 안에서 조합 + Tab으로 다음 칸
await H.fresh();
await H.tb.getByRole("button", { name: "표 넣기" }).click();
await typeKo("머리");
await page.keyboard.press("Tab");
await typeKo("칸", { leaveLast: true });
await imeKey("칸", "Tab");
await typeKo("셋");
d = await H.doc();
const cells = [];
walk(d, (n) => (n.type === "tableCell" || n.type === "tableHeader") && cells.push(textOf(n)));
check("K8. composing in table cells, Tab to next cell while composing", cells.slice(0, 3).join("|") === "머리|칸|셋", cells.slice(0, 3).join("|"));

// K9. Shift+Enter는 문단 안 줄바꿈(hardBreak = 계약 run.text의 LF)
await H.fresh();
await typeKo("윗줄");
await page.keyboard.press("Shift+Enter");
await typeKo("아랫줄");
d = await H.doc();
check("K9. Shift+Enter inserts an in-paragraph line break", blocks(d).length === 1 && textOf(blocks(d)[0]) === "윗줄\n아랫줄", JSON.stringify(textOf(blocks(d)[0] ?? {})));

// S1. 목록 4단계 이상은 거절(조용히 고치지 않고 안내)
await H.fresh();
await page.keyboard.type("- a");
for (const t of ["b", "c", "d"]) {
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await page.keyboard.type(t);
}
d = await H.doc();
check("S1. list nesting stops at 3 levels with a notice", listDepth(d) === 3 && (await H.main.getByText("목록은 3단계까지만").count()) === 1, `depth=${listDepth(d)}`);

// S2. 단 나누기 2:1, 단 안에서는 표 넣기 불가
await H.fresh();
await H.tb.getByRole("combobox", { name: "단 나누기" }).selectOption({ label: "2:1" });
await typeKo("왼쪽 단");
d = await H.doc();
const cols = blocks(d).find((b) => b.type === "columns");
check("S2. columns 2:1 inserted, text goes into first column", cols?.content?.map((c) => c.attrs.width).join(":") === "2:1" && textOf(cols.content[0]) === "왼쪽 단", JSON.stringify(cols?.content?.map((c) => c.attrs)));
check("S2. table insert disabled inside columns", await H.tb.getByRole("button", { name: "표 넣기" }).isDisabled());
await H.main.getByRole("group", { name: "단" }).getByRole("button", { name: "단 비율 1:2" }).click();
d = await H.doc();
check("S2. column ratio preset changes widths", blocks(d).find((b) => b.type === "columns")?.content?.map((c) => c.attrs.width).join(":") === "1:2");

// 붙여넣기 도우미: HTML/텍스트를 ClipboardEvent로 넣는다
async function pasteHtml(html, text = "") {
  await page.evaluate(
    ([h, t]) => {
      const dt = new DataTransfer();
      if (h) dt.setData("text/html", h);
      dt.setData("text/plain", t);
      document.querySelector(".ProseMirror").dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    },
    [html, text]
  );
}

// S3. 병합 칸이 있는 표 붙여넣기는 거절
await H.fresh();
await pasteHtml('<table><tr><td colspan="2">합친 칸</td></tr><tr><td>a</td><td>b</td></tr></table>');
d = await H.doc();
check("S3. pasting a table with merged cells is rejected with a notice", !blocks(d).some((b) => b.type === "table") && (await H.main.getByText("표 칸 병합은 지원하지 않습니다").count()) === 1, JSON.stringify(blocks(d).map((b) => b.type)));

// S4. 외부 HTML 붙여넣기: iframe·외부 img·script·style 색은 들어오지 않는다
await H.fresh();
await pasteHtml('<p>앞<img src="https://example.com/x.png">뒤</p><iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe><script>window.__x=1</script><p><span style="color: rgb(220, 38, 38); font-size: 40px">빨강</span> <span style="color: red">이름색</span></p>');
d = await H.doc();
const types = new Set();
const colors = [];
walk(d, (n) => {
  types.add(n.type);
  for (const m of n.marks ?? []) if (m.type === "textStyle") colors.push(m.attrs.color);
});
check("S4. paste drops iframe/external img/script; rgb() color normalized to hex, named color dropped", !types.has("embed") && !types.has("postImage") && !(await page.evaluate(() => window.__x)) && colors.filter(Boolean).join(",") === "#DC2626", `types=${[...types]} colors=${JSON.stringify(colors)}`);

// S5. code mark와 bold 공존, 글자 색
await H.fresh();
await page.keyboard.type("코드굵게");
await page.keyboard.press("Shift+Home");
await H.tb.getByRole("button", { name: "굵게" }).click();
await H.tb.getByRole("button", { name: "인라인 코드" }).click();
await H.tb.getByRole("button", { name: "글자 색 #2563EB" }).click();
d = await H.doc();
const marks = (blocks(d)[0]?.content?.[0]?.marks ?? []).map((m) => (m.type === "textStyle" ? `color:${m.attrs.color}` : m.type)).sort().join(",");
check("S5. code mark coexists with bold and color", marks === "bold,code,color:#2563EB", marks);

// S6. 링크 정책: javascript: 거절, https 허용
await H.fresh();
await page.keyboard.type("링크");
await page.keyboard.press("Shift+Home");
nextPrompt = ["javascript:alert(1)"];
await H.tb.getByRole("button", { name: "링크", exact: true }).click();
d = await H.doc();
const noLink = !(blocks(d)[0]?.content?.[0]?.marks ?? []).some((m) => m.type === "link");
nextPrompt = ["https://example.com/a"];
await H.tb.getByRole("button", { name: "링크", exact: true }).click();
d = await H.doc();
const link = (blocks(d)[0]?.content?.[0]?.marks ?? []).find((m) => m.type === "link");
check("S6. javascript: link refused, https link set", noLink && link?.attrs?.href === "https://example.com/a", JSON.stringify(link?.attrs));

// S7. 블록 id: 모든 블록 노드에 UUID, 같은 id를 가진 HTML을 붙여 넣으면 새 id로 바뀐다
await H.fresh();
await pasteHtml('<p data-id="11111111-1111-4111-8111-111111111111">하나</p><p data-id="11111111-1111-4111-8111-111111111111">둘</p><ul><li><p>항목</p></li></ul>');
d = await H.doc();
const ids = [];
let missing = 0;
walk(d, (n) => {
  if (n.type === "doc" || n.type === "text" || n.type === "hardBreak") return;
  if (n.attrs?.id) ids.push(n.attrs.id);
  else missing++;
});
const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
check("S7. every block node has a unique UUID; duplicated pasted ids are reissued", missing === 0 && new Set(ids).size === ids.length && ids.every((i) => uuidRe.test(i) || i === "11111111-1111-4111-8111-111111111111"), `ids=${ids.length} unique=${new Set(ids).size} missing=${missing}`);

// S8. 블록 이동(위/아래 버튼) 후 id 유지
await H.fresh();
await page.keyboard.type("A");
await page.keyboard.press("Enter");
await page.keyboard.type("B");
d = await H.doc();
const beforeIds = blocks(d).map((b) => b.attrs.id);
await H.tb.getByRole("button", { name: "블록 위로" }).click();
d = await H.doc();
check("S8. move block up swaps order and keeps ids", blocks(d).map(textOf).join("") === "BA" && blocks(d).map((b) => b.attrs.id).join() === [...beforeIds].reverse().join(), blocks(d).map(textOf).join(""));

// S9. 표: 첫 행 머리글 토글, 칸 정렬은 cell.align, 칸 배경
await H.fresh();
await H.tb.getByRole("button", { name: "표 넣기" }).click();
await H.tb.getByRole("button", { name: "가운데 정렬" }).click();
await H.main.getByRole("group", { name: "칸 배경" }).getByRole("button", { name: "칸 배경 #DBEAFE" }).click();
d = await H.doc();
const table = blocks(d).find((b) => b.type === "table");
const firstCell = table?.content?.[0]?.content?.[0];
check("S9. table: header row, cell align/background on the cell (paragraph align untouched)",
  firstCell?.type === "tableHeader" && firstCell.attrs.align === "center" && firstCell.attrs.background === "#DBEAFE" && (firstCell.content?.[0]?.attrs?.textAlign ?? "left") === "left",
  JSON.stringify(firstCell?.attrs));

// Y1. YouTube: 주소에서 ID만(시작 시각 버림), 편집 화면 카드는 재생하지 않음
await H.fresh();
nextPrompt = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"];
await H.tb.getByRole("button", { name: "YouTube 영상" }).click();
d = await H.doc();
const embed = blocks(d).find((b) => b.type === "embed");
check("Y1. YouTube URL becomes embed with 11-char id only (t= dropped)", embed?.attrs?.resourceId === "dQw4w9WgXcQ" && embed.attrs.provider === "youtube", JSON.stringify(embed?.attrs));
check("Y1. editor card has no iframe and says it does not play", (await H.editor.locator("iframe").count()) === 0 && (await H.main.getByText("편집 화면에서는 재생하지 않습니다").count()) === 1);
await page.keyboard.type("캡션");
d = await H.doc();
check("Y1. caption text goes into the embed node", textOf(blocks(d).find((b) => b.type === "embed") ?? {}) === "캡션", JSON.stringify(blocks(d).map(textOf)));

// Y2. YouTube 주소를 붙여 넣어도 embed로 자동 승격하지 않는다
await H.fresh();
await pasteHtml("", "https://youtu.be/dQw4w9WgXcQ");
d = await H.doc();
check("Y2. pasted YouTube URL is not auto-promoted to an embed", !blocks(d).some((b) => b.type === "embed") && textOf(blocks(d)[0] ?? {}).includes("youtu.be"), JSON.stringify(blocks(d).map((b) => b.type)));

// Y3. 알 수 없는 주소는 거절
await H.fresh();
nextPrompt = ["https://vimeo.com/123"];
await H.tb.getByRole("button", { name: "YouTube 영상" }).click();
d = await H.doc();
check("Y3. non-YouTube URL refused", !blocks(d).some((b) => b.type === "embed"));

// I1. 파일 선택 업로드 → imageId로 블록 삽입, 자체 이미지 주소만
await H.fresh();
await H.main.getByLabel("본문 이미지 파일").setInputFiles({ name: "사진.png", mimeType: "image/png", buffer: PNG });
await H.editor.locator("figure[data-type=post-image] img").first().waitFor({ timeout: 10000 }).catch(() => {});
d = await H.doc();
const img = blocks(d).find((b) => b.type === "postImage");
const src = await H.editor.locator("figure[data-type=post-image] img").first().getAttribute("src").catch(() => null);
check("I1. picked image uploads and inserts by imageId with alt from file name", /^[0-9a-f-]{36}$/.test(img?.attrs?.imageId ?? "") && img.attrs.alt === "사진" && src === `${BASE}/api/media/post-images/${img.attrs.imageId}`, JSON.stringify(img?.attrs));

// I2. 이미지 파일 붙여넣기
await H.fresh();
await page.evaluate((b64) => {
  const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const dt = new DataTransfer();
  dt.items.add(new File([bin], "붙여넣기.png", { type: "image/png" }));
  document.querySelector(".ProseMirror").dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
}, PNG.toString("base64"));
await H.editor.locator("figure[data-type=post-image] img").first().waitFor({ timeout: 10000 }).catch(() => {});
d = await H.doc();
check("I2. pasted image file uploads and inserts", blocks(d).some((b) => b.type === "postImage"), JSON.stringify(blocks(d).map((b) => b.type)));
let hasBlob = false;
walk(d, (n) => {
  if (JSON.stringify(n.attrs ?? {}).match(/blob:|data:/)) hasBlob = true;
});
check("I2. no blob:/data: URL in the document", !hasBlob);

// I3. GIF는 올리지 않는다
await H.fresh();
await H.main.getByLabel("본문 이미지 파일").setInputFiles({ name: "a.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a") });
check("I3. unsupported image type refused with a notice", (await H.main.getByText("PNG, JPEG, WebP 이미지만").count()) === 1);

// A1. 여러 블록을 섞은 실제 편집 결과가 post-blocks v1 검사를 통과한다(adapter)
await H.fresh();
await typeKo("첫 문단");
await page.keyboard.press("Shift+Enter");
await typeKo("둘째 줄");
await page.keyboard.press("Enter");
await page.keyboard.type("- ");
await typeKo("항목");
await page.keyboard.press("Enter");
await page.keyboard.press("Tab");
await typeKo("하위");
await page.keyboard.press("Enter");
await page.keyboard.press("Enter");
await page.keyboard.press("Enter");
await H.tb.getByRole("button", { name: "표 넣기" }).click();
await typeKo("칸");
await H.tb.getByRole("button", { name: "오른쪽 정렬" }).click();
const a1 = await H.blocks();
const a1types = (a1.doc?.blocks ?? []).map((b) => b.type).join(",");
const a1cell = a1.doc?.blocks?.find((b) => b.type === "table")?.rows?.[0];
check("A1. mixed editor output converts to valid post-blocks (LF run, nested list, header row, cell align)",
  a1.status === "post-blocks v1 검사 통과" && a1.doc.blocks[0].runs?.[0]?.text === "첫 문단\n둘째 줄" && a1types.startsWith("paragraph,list") && a1types.includes("table") &&
    a1cell?.header === true && a1cell.cells[0].align === "right" && !("align" in a1cell.cells[0].blocks[0]),
  `${a1.status} types=${a1types}`);

check("Y*. no request to YouTube/ytimg/googlevideo hosts during editing", external.length === 0, external.slice(0, 3).join(" "));
// R1. 공개 renderer: 서식·표·영상 카드. 누르기 전에는 YouTube 요청 0, 누르면 nocookie 주소의 고정 sandbox iframe
await H.fresh();
await page.keyboard.type("굵은 ");
await page.keyboard.press("Shift+Home");
await H.tb.getByRole("button", { name: "굵게" }).click();
await page.keyboard.press("End");
nextPrompt = ["https://youtu.be/dQw4w9WgXcQ"];
await H.tb.getByRole("button", { name: "YouTube 영상" }).click();
const preview = H.main.getByRole("region", { name: "공개 미리보기" });
await H.main.getByText("공개 화면 미리보기").click();
check("R1. preview renders bold as <strong> and the inactive YouTube card", (await preview.locator("strong").first().innerText().catch(() => "")).trim() === "굵은" && (await preview.getByRole("button", { name: /눌러서 재생/ }).count()) === 1);
check("R1. no YouTube request before activation (editor + preview)", external.length === 0, external.slice(0, 3).join(" "));
const firstYt = page.waitForRequest((r) => YT_HOSTS.test(new URL(r.url()).hostname), { timeout: 5000 }).catch(() => null);
await preview.getByRole("button", { name: /눌러서 재생/ }).click();
const frame = preview.locator("iframe");
const fsrc = await frame.getAttribute("src").catch(() => null);
const fsandbox = await frame.getAttribute("sandbox").catch(() => null);
check("R1. activation opens youtube-nocookie iframe with the validated id and fixed sandbox", fsrc === "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?autoplay=1" && fsandbox === "allow-scripts allow-same-origin allow-presentation", `${fsrc} | ${fsandbox}`);
const first = await firstYt;
check("R1. the first YouTube-side request after activation is the nocookie embed", first?.url() === fsrc, first?.url() ?? "(none)");

check("no page errors (chromium)", pageErrors.length === 0, pageErrors.slice(0, 3).join(" | "));
await browser.close();

// ======================= WebKit · Firefox: 조합 없는 기본 편집 =======================
for (const [name, type] of [["webkit", webkit], ["firefox", firefox]]) {
  const b = await type.launch();
  const ctx = await b.newContext();
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(e.message));
  const h = harness(p);
  await login(p);
  await h.fresh();
  // 로그인 홈에서 lab으로 옮길 때 WebKit이 취소된 RSC prefetch(/posts·/tags ?_rsc)를 pageerror로 남긴다(편집기 이전 단계).
  // 편집기와 무관한 기존 탐색 동작이라 숨기지 않고 정보로만 출력하며, 편집기를 연 뒤의 오류는 그대로 실패로 센다.
  if (errs.length) console.log(`INFO  ${name}: page errors before the editor (navigation from home): ${errs.slice(0, 2).join(" | ")}`);
  errs.length = 0;
  await p.keyboard.insertText("한글 입력");
  await p.keyboard.press("Enter");
  await p.keyboard.type("- ");
  await p.keyboard.insertText("항목");
  await p.keyboard.press("Enter");
  await p.keyboard.press("Tab");
  await p.keyboard.insertText("하위");
  const dd = await h.doc();
  const bs = blocks(dd);
  check(`W. ${name}: Korean insertText, paragraph split, list nesting`, textOf(bs[0] ?? {}) === "한글 입력" && bs[1]?.type === "bulletList" && listDepth(dd) === 2, `${bs.map((x) => x.type).join(",")} depth=${listDepth(dd)}`);
  check(`W. ${name}: no page errors while editing`, errs.length === 0, errs.slice(0, 2).join(" | "));
  await b.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
