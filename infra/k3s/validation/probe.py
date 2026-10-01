"""Runs INSIDE a disposable probe Pod via kubectl exec; no host Python needed."""
import socket
import ssl
import struct
import sys
import urllib.request

kind, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    if kind == "tcp":
        with socket.create_connection((host, port), timeout=3):
            pass
    elif kind == "https":
        # Avoid ambient proxy configuration and verify TLS certificates.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"https://{host}:{port}/", timeout=10) as response:
            if response.status != 200:
                raise ValueError(f"unexpected HTTP {response.status}")
    elif kind in ("dns-udp", "dns-tcp"):
        ident = 0x7351
        question = b"".join(bytes([len(part)]) + part.encode() for part in "example.com".split("."))
        query = struct.pack("!6H", ident, 0x0100, 1, 0, 0, 0) + question + b"\0" + struct.pack("!2H", 1, 1)
        def receive_exact(sock, count):
            buf = b""
            while len(buf) < count:
                part = sock.recv(count - len(buf))
                if not part:
                    raise ValueError("truncated DNS response")
                buf += part
            return buf
        if kind == "dns-udp":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(3)
                sock.sendto(query, (host, port))
                answer, _ = sock.recvfrom(4096)
        else:
            with socket.create_connection((host, port), timeout=3) as sock:
                sock.sendall(struct.pack("!H", len(query)) + query)
                size = struct.unpack("!H", receive_exact(sock, 2))[0]
                answer = receive_exact(sock, size)
        reply_id, flags, _, answer_count, _, _ = struct.unpack("!6H", answer[:12])
        if reply_id != ident or flags & 0xF or not flags & 0x8000 or not answer_count:
            raise ValueError("invalid/empty DNS answer")
    else:
        raise ValueError(f"unknown probe type {kind}")
except TimeoutError:
    print("TIMEOUT", flush=True)
    sys.exit(2)
except Exception as exc:
    # Do not emit response bodies, metadata or credentials.
    print(f"ERROR {type(exc).__name__}", flush=True)
    sys.exit(3)
print("CONNECTED", flush=True)
