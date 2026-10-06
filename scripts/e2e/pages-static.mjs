// GitHub Pages 프로젝트 사이트 흉내: `${PREFIX}/` 아래에서 out/을 서빙. 디렉터리 -> index.html, 없으면 404.html(404).
// /api 같은 서버 경로는 없다.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.env.OUT_DIR ?? "/out";
const PREFIX = process.env.PREFIX ?? "";
const PORT = Number(process.env.PORT ?? 8080);
const types = { ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css", ".json": "application/json", ".txt": "text/plain", ".woff2": "font/woff2", ".ico": "image/x-icon", ".svg": "image/svg+xml" };

function send(res, status, file) {
  res.writeHead(status, { "Content-Type": types[path.extname(file)] ?? "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}
http
  .createServer((req, res) => {
    const p = decodeURIComponent(new URL(req.url, "http://x").pathname);
    const notFound = () => send(res, 404, path.join(ROOT, "404.html"));
    if (PREFIX && p !== PREFIX && !p.startsWith(`${PREFIX}/`)) return notFound();
    const rel = p.slice(PREFIX.length) || "/";
    const file = path.join(ROOT, path.normalize(rel));
    if (!file.startsWith(ROOT)) return notFound();
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) {
      if (!rel.endsWith("/")) {
        res.writeHead(301, { Location: `${p}/` });
        return res.end();
      }
      const idx = path.join(file, "index.html");
      return fs.existsSync(idx) ? send(res, 200, idx) : notFound();
    }
    if (fs.existsSync(file)) return send(res, 200, file);
    return notFound();
  })
  .listen(PORT, "0.0.0.0", () => console.log(`pages-static :${PORT} prefix='${PREFIX}'`));
