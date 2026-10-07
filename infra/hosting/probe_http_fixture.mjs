// Disposable local HTTP failures in the unchanged web runtime image.
import http from 'node:http';
import fs from 'node:fs';
const mode = process.argv[2];
const ready = () => fs.writeFileSync('/tmp/probe-fixture-ready', 'ready');
if (mode === 'refused') {
  ready();
  setInterval(() => {}, 1000);
} else {
  http.createServer((req, res) => {
    const respond = () => {
      res.writeHead(mode === 'non200' ? 503 : 200);
      res.end('{}');
    };
    if (mode === 'delayed') setTimeout(respond, 10000);
    else respond();
  }).listen(8080, '127.0.0.1', ready);
}
