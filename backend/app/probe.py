import json
import sys
import urllib.request


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/readyz"
    if path not in {"/readyz", "/healthz"}:
        return 1
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8080" + path, timeout=2) as result:
            return 0 if result.status == 200 and json.load(result) == {"status": "ok"} else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
