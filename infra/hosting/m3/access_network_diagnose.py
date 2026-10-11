"""Anonymous DNS/TCP/TLS only; no HTTP, token, source or native operation."""
import json
import queue
import socket
import ssl
import threading


def probe(host, port, tls=False):
    result = {'dns': 'not_tested', 'tcp': 'not_tested', 'tls': 'not_tested'}
    values = queue.Queue()
    def resolve():
        try:
            values.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            values.put(None)
    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = values.get(timeout=3)
    except queue.Empty:
        return {**result, 'dns': 'timeout'}
    if not addresses:
        return {**result, 'dns': 'failed'}
    result['dns'] = 'resolved'
    family, kind, proto, _, address = addresses[0]
    try:
        with socket.socket(family, kind, proto) as connection:
            connection.settimeout(3)
            connection.connect(address)
            result['tcp'] = 'connected'
            if tls:
                try:
                    with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
                        result['tls'] = 'verified'
                except ssl.SSLCertVerificationError:
                    result['tls'] = 'verification_failed'
                except Exception:
                    result['tls'] = 'failed'
    except Exception:
        result['tcp'] = 'failed'
    return result


if __name__ == '__main__':
    print(json.dumps({'sourceCredentialRead': False, 'httpOrAuthRpc': False, 'nativeStarted': False,
                      'codexBackend': probe('chatgpt.com', 443, tls=True),
                      'callbackService': probe('api.m2-hosting.svc.cluster.local', 8080)}))
