"""Appended to reviewed own-PID1 helpers; no DNS/HTTP/auth/native execution."""
import ipaddress
import socket
import ssl


def holder(nonce):
    if os.getpid() != 1 or os.getuid() != 10001: raise ValueError()
    ticks, start, digest = own_pid1()
    print(json.dumps({'holding': True, 'networkRequested': False,
        'podUid': os.environ[ENV_UID], 'podName': os.environ[ENV_NAME], 'nonce': nonce,
        'startTicks': ticks, 'startNs': start, 'cmdlineSha256': digest,
        'timeNamespace': str(Path('/proc/self/ns/time').readlink()),
        'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}), flush=True)
    remaining = start+28_000_000_000-time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if remaining <= 0: raise ValueError()
    time.sleep(remaining/1_000_000_000)


class NumericDeadline(BaseException):
    pass


def numeric_once(expected, socket_factory=socket.socket, context_factory=ssl.create_default_context):
    start = verify_owner(expected)
    lease = expected['lease']
    if (str(Path('/proc/self/ns/time').readlink()) != lease['timeNamespace']
            or Path('/proc/sys/kernel/random/boot_id').read_text().strip() != lease['bootId']):
        raise ValueError()
    provider = ipaddress.IPv4Address(lease['providerIp'])
    callback = ipaddress.IPv4Address(expected['callbackIp'])
    if (not provider.is_global or provider.is_private or provider.is_reserved or provider.is_multicast
            or provider.is_loopback or provider.is_link_local or provider.is_unspecified): raise ValueError()
    if callback.is_loopback or callback.is_link_local or callback.is_multicast or callback.is_unspecified:
        raise ValueError()
    end = min(start+28_000_000_000, expected['jobDeadlineNs'], lease['expiryNs'])
    def check(seconds):
        verify_owner(expected)
        now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if (now < lease['endNs'] or end-now < seconds*1_000_000_000
                or abs((time.time_ns()-lease['realtimeNs'])-(now-lease['endNs'])) > 250_000_000):
            raise NumericDeadline()
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if now >= start+18_000_000_000: raise NumericDeadline()
    check(10)
    marker = '/dev/shm/numeric-tls-once-'+expected['nonce']
    with os.fdopen(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), 'w') as out:
        out.write('consumed\n')
    report = {'callback': 'not_attempted', 'provider': 'not_attempted', 'providerAttempts': 0,
              'dnsQueries': 0, 'httpBytes': 0, 'authRpc': False, 'nativeStarted': False,
              'sourceCredentialRead': False, 'nonce': expected['nonce'],
              'podUid': expected['podUid'], 'jobUid': expected['jobUid'],
              'context': expected['context'], 'enteredNs': now}
    def expired(*_): raise NumericDeadline()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        check(10)
        try:
            with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as channel:
                channel.settimeout(3)
                check(10)
                if time.clock_gettime_ns(time.CLOCK_BOOTTIME) >= start+18_000_000_000:
                    raise NumericDeadline()
                channel.connect((str(callback), 8080))
            report['callback'] = 'tcp_connected'
        except (OSError, ValueError):
            report['callback'] = 'failed'; return report
        check(6)
        report['providerAttempts'] = 1
        try:
            with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as channel:
                channel.settimeout(3); channel.connect((str(provider), 443))
                check(3)
                context = context_factory()
                if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
                    raise ValueError()
                # CA loading/validation may itself consume time. The check
                # guarding actual ClientHello must follow that preparation.
                check(3)
                with context.wrap_socket(channel, server_hostname='chatgpt.com') as secure:
                    if not secure.getpeercert(): raise ValueError()
                report['provider'] = 'tls_verified'
        except ssl.SSLCertVerificationError:
            report['provider'] = 'certificate_failed'
        except (OSError, ValueError):
            report['provider'] = 'failed'
        check(0)
        return report
    except NumericDeadline:
        report['deadlineExceeded'] = True
        return report
    finally:
        report['finishedNs'] = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
