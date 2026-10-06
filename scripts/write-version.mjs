// 웹 이미지 build metadata를 public/version.json으로 기록한다.
// 웹 Dockerfile이 `pnpm run build` 전에 실행한다. 형식은 API GET /api/version과 같다.
import fs from "fs";
import path from "path";

const sha = process.env.WEB_GIT_SHA ?? "";
const builtAt = process.env.WEB_BUILT_AT ?? "";

const errors = [];
if (!/^[0-9a-f]{40}$/.test(sha)) {
  errors.push("WEB_GIT_SHA must be a full 40-character lowercase git commit SHA");
}
if (
  !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(builtAt) ||
  Number.isNaN(Date.parse(builtAt))
) {
  errors.push("WEB_BUILT_AT must be an RFC3339 UTC timestamp like 2026-10-06T00:00:00Z");
}
if (errors.length > 0) {
  for (const e of errors) console.error(`version:write: ${e}`);
  process.exit(1);
}

const outFile = path.join(process.cwd(), "public", "version.json");
fs.writeFileSync(outFile, `${JSON.stringify({ sha, builtAt })}\n`);
console.log(`version:write: wrote ${path.relative(process.cwd(), outFile)} (${sha.slice(0, 12)}, ${builtAt})`);
