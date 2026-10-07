"""Disposable local HTTP failures in the unchanged API runtime image."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import time

mode = sys.argv[1]
if mode == "refused":
    Path("/tmp/probe-fixture-ready").touch()
    time.sleep(300)
else:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if mode == "delayed":
                time.sleep(10)
            self.send_response(503 if mode == "non200" else 200)
            self.end_headers()
            try:
                self.wfile.write(b'{"status":"ok"}')
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 8080), Handler)
    Path("/tmp/probe-fixture-ready").touch()
    server.serve_forever()
