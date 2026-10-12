"""Run as PID1 in actual Runner/network-none; child sockets are substitutes."""
import json
import os
from pathlib import Path
import subprocess

from access_numeric_prepare import runtime_source

exec(runtime_source(), globals())


def holder(nonce):
    ticks, start, digest = own_pid1()
    command = Path('/proc/1/cmdline').read_bytes().decode().rstrip('\0').split('\0')
    now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    expected = {'holderCommand': command, 'startTicks': ticks, 'startNs': start,
        'cmdlineSha256': digest, 'nonce': nonce, 'podUid': os.environ[ENV_UID],
        'podName': os.environ[ENV_NAME], 'jobUid': 'fixture-job', 'callbackIp': '10.43.0.7',
        'jobDeadlineNs': start+30_000_000_000, 'context': {'fixture': True},
        'lease': {'providerIp': '1.1.1.1', 'expiryNs': now+60_000_000_000, 'endNs': now,
                  'realtimeNs': time.time_ns(), 'timeNamespace': str(Path('/proc/self/ns/time').readlink()),
                  'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}}
    substitute = '''
calls=[]; closes=[]
socket.getaddrinfo=lambda *a,**k: (_ for _ in ()).throw(AssertionError('no DNS'))
class Channel:
    def __enter__(self): return self
    def __exit__(self,*a): closes.append(True)
    def settimeout(self,n): assert n==3
    def connect(self,target): calls.append(target)
class Secure(Channel):
    def getpeercert(self): return {'fixture':True}
class Context:
    verify_mode=ssl.CERT_REQUIRED
    check_hostname=True
    def wrap_socket(self,channel,server_hostname):
        assert server_hostname=='chatgpt.com'
        return Secure()
real=ssl.create_default_context()
assert real.verify_mode==ssl.CERT_REQUIRED and real.check_hostname and real.get_ca_certs()
first=numeric_once(EXPECTED,lambda *a:Channel(),Context)
assert first['callback']=='tcp_connected' and first['provider']=='tls_verified'
assert calls==[('10.43.0.7',8080),('1.1.1.1',443)] and len(closes)==3
try:numeric_once(EXPECTED,lambda *a:Channel(),Context)
except FileExistsError:pass
else:raise AssertionError('replay')
assert len(calls)==2
print(json.dumps({'actualRunnerRuntimeValidated':True,'networkCallsWereSubstitutes':True,
                  'realDefaultCaContextValidated':True,'singleUseClaimVerified':True,
                  'dnsQueries':0,'liveProviderSuccess':False}))
'''
    child = runtime_source()+'\nEXPECTED='+repr(expected)+'\n'+substitute
    run = subprocess.run(['python', '-B', '-c', child], capture_output=True, text=True, timeout=12)
    if run.returncode: raise ValueError('numeric actual-image substitute failed')
    print(run.stdout.strip(), flush=True)


holder("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
