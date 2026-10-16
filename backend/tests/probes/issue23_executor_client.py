import ast,socket,json,uuid,time
path='/run/shell-executor/command.sock'
def request(command,cwd='.',wait=True):
 s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(10);s.connect(path)
 s.sendall(json.dumps({'version':1,'requestId':str(uuid.uuid4()),'command':command,'cwd':cwd}).encode()+b'\n')
 if not wait:return s
 b=b''
 while not b.endswith(b'\n'):b+=s.recv(8192)
 s.close();return json.loads(b)
for _ in range(100):
 try:
  r=request('id -u; test ! -e /control; test ! -e /transport; test ! -e /state/codex; echo isolated')
  break
 except (FileNotFoundError,ConnectionRefusedError):time.sleep(.05)
assert r['status']=='completed' and r['stdout']=='20002\nisolated\n',r
r=request('echo sample > example; cat example');assert r['stdout']=='sample\n',r
assert request('true','/tmp')['error']['code']=='shell_invalid_request'
assert request('true','../')['error']['code']=='shell_invalid_request'
r=request('python -c "print(chr(120)*20000)"');assert r['truncated'] and len(r['stdout'].encode())<=1024,r
pending=request('sleep 10',wait=False);time.sleep(.1)
assert request('echo bad')['error']['code']=='shell_busy'
b=b''
while not b.endswith(b'\n'):b+=pending.recv(8192)
pending.close();r=json.loads(b);assert r['status']=='timed_out' and r['error']['code']=='shell_timeout',r
# setsid/double-fork descendant must not survive the lease.
r=request("python -c 'import os,time; p=os.fork(); p and os._exit(0); os.setsid(); os.fork() and os._exit(0); time.sleep(30)'")
assert r['status']=='timed_out',r
r=request("python -c 'import os; print(sorted(p for p in os.listdir(\"/proc\") if p.isdigit()))'")
assert r['status']=='completed',r
assert '1' in ast.literal_eval(r['stdout']) and len(ast.literal_eval(r['stdout'])) <= 3,r
r=request('sleep 30 >/dev/null 2>&1 & echo parent-finished')
assert r['status']=='completed' and r['stdout']=='parent-finished\n',r
r=request("python -c 'import os; print(sorted(p for p in os.listdir(\"/proc\") if p.isdigit()))'")
assert len(ast.literal_eval(r['stdout'])) <= 3,r
r=request('cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max')
assert r['stdout'].splitlines()==['134217728','50000 100000'],r
# Connection loss cancels and frees the lease after cleanup.
s=request('sleep 10',wait=False);time.sleep(.1);s.close();time.sleep(.3)
assert request('echo recovered')['stdout']=='recovered\n'
# Command UID is not permitted to recursively call its own executor.
r=request("python -c 'import socket; s=socket.socket(socket.AF_UNIX); s.connect(\"/run/shell-executor/command.sock\"); print(s.recv(4096).decode())'")
assert 'shell_peer_refused' in r['stdout'],r
print('executor boundary, descendant cleanup and cgroup limit probes passed')
