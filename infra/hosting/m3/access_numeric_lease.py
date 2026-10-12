"""Strict single-response A/CNAME lease. DNS/raw addresses are private evidence."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import struct
import time

HOST = 'chatgpt.com'


def public_ipv4(value):
    ip = ipaddress.IPv4Address(value)
    if (str(ip) != value or not ip.is_global or ip.is_multicast or ip.is_reserved
            or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_private):
        raise ValueError()
    return value


def name(packet, offset):
    labels, seen, following = [], set(), None
    for _ in range(128):
        if offset in seen or offset >= len(packet): raise ValueError()
        seen.add(offset); length = packet[offset]; offset += 1
        if length & 0xc0 == 0xc0:
            if offset >= len(packet): raise ValueError()
            pointer = ((length & 63) << 8) | packet[offset]
            if pointer >= offset-1: raise ValueError()
            if following is None: following = offset+1
            offset = pointer; continue
        if length & 0xc0: raise ValueError()
        if length == 0:
            result = '.'.join(labels).lower()
            if len(result) > 253: raise ValueError()
            return result, following if following is not None else offset
        if length > 63 or offset+length > len(packet): raise ValueError()
        label = packet[offset:offset+length].decode('ascii')
        if not re.fullmatch('[A-Za-z0-9_-]+', label): raise ValueError()
        labels.append(label); offset += length
    raise ValueError()


def answer(packet, identity):
    if not 12 <= len(packet) <= 4096 or packet[:2] != identity: raise ValueError()
    flags, qd, an, ns, ar = struct.unpack('!HHHHH', packet[2:12])
    if not flags & 0x8000 or flags & 0x7a0f or qd != 1 or not 1 <= an <= 24 or ns+ar > 32:
        raise ValueError()
    question, offset = name(packet, 12)
    if question != HOST or packet[offset:offset+4] != struct.pack('!HH', 1, 1): raise ValueError()
    offset += 4; records = []
    for index in range(an+ns+ar):
        owner, offset = name(packet, offset)
        if offset+10 > len(packet): raise ValueError()
        kind, cls, ttl, size = struct.unpack('!HHIH', packet[offset:offset+10]); offset += 10
        end = offset+size
        if end > len(packet): raise ValueError()
        if index < an:
            if cls != 1 or ttl <= 0 or kind not in (1, 5): raise ValueError()
            if kind == 1:
                if size != 4: raise ValueError()
                value = public_ipv4(str(ipaddress.IPv4Address(packet[offset:end])))
            else:
                value, consumed = name(packet, offset)
                if consumed != end or not value: raise ValueError()
            records.append((owner, kind, ttl, value))
        offset = end
    if offset != len(packet): raise ValueError()
    current, owners, ttls = HOST, [], []
    for _ in range(9):
        if current in owners: raise ValueError()
        owners.append(current)
        group = [r for r in records if r[0] == current]
        if not group: raise ValueError()
        cname = [r for r in group if r[1] == 5]
        addresses = [r for r in group if r[1] == 1]
        ttls.extend(r[2] for r in group)
        if cname:
            if len(cname) != 1 or addresses: raise ValueError()
            current = cname[0][3]; continue
        if not 1 <= len(addresses) <= 8 or any(r[0] not in owners for r in records): raise ValueError()
        selected = min((r[3] for r in addresses), key=lambda v: int(ipaddress.IPv4Address(v)))
        return selected, min(ttls), len(owners)-1
    raise ValueError()


def lease(capture, binding, now_ns, reserve=15):
    if capture['binding'] != binding or capture['peer'] != binding['resolverIp']:
        raise ValueError()
    if (capture['bootId'] != binding['bootId'] or type(capture.get('realtimeNs')) is not int or capture['realtimeNs'] <= 0
            or not isinstance(capture.get('timeNamespace'), str) or not re.fullmatch(r'time:\[[0-9]+\]', capture['timeNamespace'])): raise ValueError()
    begin, end = capture['beginNs'], capture['endNs']
    if (any(type(v) is not int for v in (begin, end, now_ns)) or begin <= 0
            or not begin <= end <= now_ns or end-begin > 3_000_000_000): raise ValueError()
    packet = base64.b64decode(capture['response'], validate=True)
    identity = bytes.fromhex(capture['queryId'])
    if len(identity) != 2: raise ValueError()
    ip, ttl, depth = answer(packet, identity)
    expiry = begin+min(ttl, 60)*1_000_000_000
    if reserve not in (10, 15) or expiry-now_ns < reserve*1_000_000_000: raise ValueError()
    return {'binding': binding, 'providerIp': ip, 'expiryNs': expiry, 'beginNs': begin,
            'ttlSeconds': ttl, 'cnameDepth': depth, 'timeNamespace': capture['timeNamespace'],
            'bootId': capture['bootId'], 'realtimeNs': capture['realtimeNs'], 'endNs': end}


def collect(binding):
    # Selected existing API Pod only. No original process/state/files are read.
    ipaddress.IPv4Address(binding['resolverIp'])
    identity = os.urandom(2)
    q = b''.join(bytes([len(s)])+s.encode() for s in HOST.split('.'))+b'\0'+struct.pack('!HH', 1, 1)
    request = identity+struct.pack('!HHHHH', 0x100, 1, 0, 0, 0)+q
    begin = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as channel:
        channel.settimeout(2)
        channel.connect((binding['resolverIp'], 53))
        peer = channel.getpeername()[0]
        channel.sendall(struct.pack('!H', len(request))+request)
        def exact(n):
            result = b''
            while len(result) < n:
                value = channel.recv(n-len(result))
                if not value: raise ValueError()
                result += value
            return result
        size = struct.unpack('!H', exact(2))[0]
        if not 12 <= size <= 4096: raise ValueError()
        packet = exact(size)
    end = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    answer(packet, identity)
    return {'binding': binding, 'peer': peer, 'queryId': identity.hex(),
            'response': base64.b64encode(packet).decode(), 'beginNs': begin, 'endNs': end,
            'realtimeNs': time.time_ns(), 'timeNamespace': str(Path('/proc/self/ns/time').readlink()),
            'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
