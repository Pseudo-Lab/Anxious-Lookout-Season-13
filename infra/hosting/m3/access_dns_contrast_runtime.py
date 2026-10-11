"""Diagnostic PID1 identity and bounded, single-use DNS exec. No original processes."""
import hashlib
import json
import os
from pathlib import Path
import signal
import time

ENV_UID = 'DNS_CONTRAST_POD_UID'
ENV_NAME = 'DNS_CONTRAST_POD_NAME'


def own_pid1():
    raw = Path('/proc/1/stat').read_text()
    if len(raw) > 8192 or not raw.startswith('1 ('):
        raise ValueError()
    ticks = int(raw[raw.rindex(')')+2:].split()[19])
    hz = os.sysconf('SC_CLK_TCK')
    if ticks <= 0 or type(hz) is not int or not 1 <= hz <= 10000:
        raise ValueError()
    command = Path('/proc/1/cmdline').read_bytes()
    if not command or len(command) > 65536:
        raise ValueError()
    return ticks, ticks*1_000_000_000//hz, hashlib.sha256(command).hexdigest()


def window(start_ns, now_ns, first=True):
    if (type(start_ns) is not int or type(now_ns) is not int or now_ns < start_ns
            or now_ns >= start_ns + (28 if first else 38)*1_000_000_000):
        raise ValueError()


def holder(nonce):
    if os.getpid() != 1 or os.getuid() != 10001:
        raise ValueError()
    ticks, start_ns, digest = own_pid1()
    uid, name = os.getenv(ENV_UID), os.getenv(ENV_NAME)
    if not uid or not name:
        raise ValueError()
    print(json.dumps({'holding': True, 'networkRequested': False, 'podUid': uid, 'podName': name,
                      'startTicks': ticks, 'startNs': start_ns, 'cmdlineSha256': digest, 'nonce': nonce}), flush=True)
    remaining = start_ns+40_000_000_000-time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if remaining <= 0:
        raise ValueError()
    time.sleep(remaining/1_000_000_000)


def verify_owner(expected):
    if os.getpid() == 1 or os.getuid() != 10001:
        raise ValueError()
    if os.getenv(ENV_UID) != expected['podUid'] or os.getenv(ENV_NAME) != expected['podName']:
        raise ValueError()
    ticks, start_ns, digest = own_pid1()
    command = expected['holderCommand']
    if (command[:3] != ['python', '-B', '-c'] or len(command) != 4
            or not command[3].endswith('\nholder('+json.dumps(expected['nonce'])+')\n')
            or hashlib.sha256(('\0'.join(command)+'\0').encode()).hexdigest() != digest
            or type(expected['startTicks']) is not int or type(expected['startNs']) is not int
            or ticks != expected['startTicks'] or start_ns != expected['startNs']
            or digest != expected['cmdlineSha256']):
        raise ValueError()
    return start_ns


def run_once(expected, dns_namespace):
    # Own ephemeral runtime tmpfs marker only: no PVC, source state or IPC listener.
    start_ns = verify_owner(expected)
    window(start_ns, time.clock_gettime_ns(time.CLOCK_BOOTTIME))
    nonce = expected['nonce']
    if len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
        raise ValueError()
    marker = '/dev/shm/dns-contrast-once-'+nonce
    with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as claim:
        claim.write('claimed\n')
    # A transport/ACK failure leaves this claim consumed; never replay the exec.
    start_ns = verify_owner(expected)
    window(start_ns, time.clock_gettime_ns(time.CLOCK_BOOTTIME))
    deadline = dns_namespace['RunnerDnsDeadline']
    socket_module = dns_namespace['socket']
    original_socket = socket_module.socket
    started = False

    def before_network(mark=True):
        nonlocal started
        try:
            verify_owner(expected)
            window(start_ns, time.clock_gettime_ns(time.CLOCK_BOOTTIME), first=not started)
        except Exception:
            raise deadline()
        if mark:
            started = True

    class GuardedSocket(original_socket):
        def connect(self, *args, **kwargs):
            # UDP connect does not send a DNS datagram; retain the first-send cutoff.
            before_network(mark=self.type == socket_module.SOCK_STREAM)
            return super().connect(*args, **kwargs)

        def send(self, *args, **kwargs):
            before_network()
            return super().send(*args, **kwargs)

        def sendall(self, *args, **kwargs):
            before_network()
            return super().sendall(*args, **kwargs)

    def expired(*_):
        raise deadline()

    signal.signal(signal.SIGALRM, expired)
    socket_module.socket = GuardedSocket
    try:
        remaining = (start_ns+38_000_000_000-time.clock_gettime_ns(time.CLOCK_BOOTTIME))/1_000_000_000
        window(start_ns, time.clock_gettime_ns(time.CLOCK_BOOTTIME))
        signal.setitimer(signal.ITIMER_REAL, min(10, remaining))
        result = dns_namespace['runner_image_dns'](lambda: Path('/etc/resolv.conf').read_text(), dns_namespace['query'])
        window(start_ns, time.clock_gettime_ns(time.CLOCK_BOOTTIME), first=False)
        return result
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        socket_module.socket = original_socket
