"""Only two existing allowed DNS names, configured resolver, UDP/TCP/absolute libc.

No extra hostname/search expansion query is constructed, no public resolver,
TLS/HTTP/auth/native/state/source access. Addresses and packets never print.
"""
import ipaddress
import json
import os
import queue
import socket
import struct
import threading
from pathlib import Path

TARGETS = ('chatgpt.com', 'api.m2-hosting.svc.cluster.local')


def configuration(text):
    servers, searches, ndots = [], [], 1
    for line in text.splitlines():
        words = line.split('#', 1)[0].split()
        if not words:
            continue
        if words[0] == 'nameserver' and len(words) == 2:
            ipaddress.ip_address(words[1])
            servers.append(words[1])
        elif words[0] == 'search':
            searches = words[1:]
        elif words[0] == 'options':
            for option in words[1:]:
                if option.startswith('ndots:'):
                    ndots = int(option.split(':')[1])
    return servers, len(searches), ndots


def query(name, server, tcp=False, port=53):
    if name not in TARGETS:
        raise ValueError()
    identity = os.urandom(2)
    question = b''.join(bytes([len(label)])+label.encode('ascii') for label in name.split('.'))+b'\0'+struct.pack('!HH', 1, 1)
    packet = identity+struct.pack('!HHHHH', 0x100, 1, 0, 0, 0)+question
    report = {'status': 'not_tested', 'rcode': None, 'answersPresent': False}
    family = socket.AF_INET6 if ipaddress.ip_address(server).version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as source:
            source.settimeout(2)
            source.connect((server, port))
            if tcp:
                source.sendall(struct.pack('!H', len(packet))+packet)
                def exact(size):
                    body = b''
                    while len(body) < size:
                        block = source.recv(size-len(body))
                        if not block:
                            raise ValueError()
                        body += block
                    return body
                size = struct.unpack('!H', exact(2))[0]
                if not 12 <= size <= 4096:
                    raise ValueError()
                response = exact(size)
            else:
                source.send(packet)
                response = source.recv(4096)
            if len(response) < 12 or response[:2] != identity:
                raise ValueError()
            flags, questions, answers, _, _ = struct.unpack('!HHHHH', response[2:12])
            if not flags & 0x8000 or questions != 1 or response[12:12+len(question)] != question:
                raise ValueError()
            return {'status': 'reply', 'rcode': flags & 15, 'answersPresent': answers > 0}
    except socket.timeout:
        return {**report, 'status': 'timeout'}
    except Exception:
        return {**report, 'status': 'failed'}


def libc(name):
    result = queue.Queue()
    def resolve():
        try:
            result.put(bool(socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)))
        except Exception:
            result.put(False)
    threading.Thread(target=resolve, daemon=True).start()
    try:
        return 'resolved' if result.get(timeout=2) else 'failed'
    except queue.Empty:
        return 'timeout'


def diagnose():
    servers, searches, ndots = configuration(Path('/etc/resolv.conf').read_text())
    report = {'sourceCredentialRead': False, 'stateModified': False, 'nativeStarted': False,
              'httpOrAuthRpc': False, 'configuredResolvers': len(servers), 'searchSuffixCount': searches, 'ndots': ndots}
    if not servers:
        return report
    for key, name in zip(('codexBackend', 'callbackService'), TARGETS):
        report[key] = {'absoluteUdp': query(name, servers[0]), 'absoluteTcp': query(name, servers[0], tcp=True),
                       'absoluteLibc': libc(name+'.'), 'relativeLibc': libc(name)}
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(diagnose()))
    except Exception:
        raise SystemExit('Scoped DNS diagnosis refused; resolver/packet details are not printed')
