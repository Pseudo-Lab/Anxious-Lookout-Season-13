"""#97 single-destination L4 relay, no TLS termination or payload logging.

Only a separate guest Docker network namespace may run this after review/PM
approval. No host networking, Kubernetes policy changes or production use.
"""
import select
import socket
import signal
import sys
import time


def main():
    signal.signal(signal.SIGALRM, lambda *args: sys.exit(0))
    signal.alarm(600)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # The new Docker --network=none namespace receives only the reviewed
        # veth address. Binding before that link exists avoids a setup race.
        listener.bind(('0.0.0.0', 443))
        listener.listen(4)
        listener.settimeout(10)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                with client, socket.create_connection(('10.0.0.107', 443), timeout=5) as upstream:
                    until = min(deadline, time.monotonic() + 10)
                    while time.monotonic() < until:
                        ready, _, _ = select.select([client, upstream], [], [], 1)
                        if not ready:
                            continue
                        ended = False
                        for source in ready:
                            payload = source.recv(16384)
                            if not payload:
                                ended = True
                                break
                            target = upstream if source is client else client
                            target.settimeout(2)
                            target.sendall(payload)
                        if ended:
                            break
            except OSError:
                # A TLS negative control may reset this connection. Drop it;
                # never retry/replay any bytes or log their contents.
                continue


if __name__ == '__main__':
    main()
