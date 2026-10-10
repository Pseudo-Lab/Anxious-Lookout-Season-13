"""Authless native stock UDS probe. Run only inside network-none Docker."""
import base64, hashlib, json, os, socket, sqlite3, struct, subprocess, time
from pathlib import Path

root=Path('/tmp/stock-two-client'); root.mkdir(mode=0o700)
for name in ('home','codex','original','web'): (root/name).mkdir(mode=0o700)
path=str(root/'control.sock')
args=['/usr/local/bin/codex','app-server','--listen','unix://'+path]
for value in ['cli_auth_credentials_store="ephemeral"','features.daemon_auto_start=false','features.code_mode_host=false','analytics.enabled=false','otel.metrics_exporter="none"','model="gpt-6.1-sol"']:
    args+=['-c',value]
p=subprocess.Popen(args,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(root/'home'),'CODEX_HOME':str(root/'codex'),'LANG':'C.UTF-8'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
class Client:
    def __init__(self,label):
        self.s=socket.socket(socket.AF_UNIX); self.s.settimeout(5)
        self.s.connect(path); key=base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+key+'\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        self.buf=b''
        while b'\r\n\r\n' not in self.buf: self.buf+=self.s.recv(4096)
        header,self.buf=self.buf.split(b'\r\n\r\n',1)
        assert header.split(b'\r\n')[0].endswith(b'101 Switching Protocols')
        expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        assert expected in header
        self.events=[]; self.errors=[]; self.next_id=1
        self.rpc('initialize',{'clientInfo':{'name':'fixture_'+label,'version':'1'},'capabilities':{'experimentalApi':True}})
        self.send({'method':'initialized'})
    def take(self,n):
        while len(self.buf)<n:
            block=self.s.recv(65536)
            if not block: raise EOFError()
            self.buf+=block
        result,self.buf=self.buf[:n],self.buf[n:]; return result
    def frame(self,payload,opcode=1):
        mask=os.urandom(4); length=len(payload)
        header=bytes([128|opcode,128|length]) if length<126 else (bytes([128|opcode,254])+struct.pack('!H',length) if length<65536 else bytes([128|opcode,255])+struct.pack('!Q',length))
        self.s.sendall(header+mask+bytes(v^mask[i%4] for i,v in enumerate(payload)))
    def send(self,value): self.frame(json.dumps(value).encode())
    def receive(self):
        while True:
            a,b=self.take(2); opcode=a&15; length=b&127
            if length==126: length=struct.unpack('!H',self.take(2))[0]
            elif length==127: length=struct.unpack('!Q',self.take(8))[0]
            mask=self.take(4) if b&128 else None; payload=self.take(length)
            if mask: payload=bytes(v^mask[i%4] for i,v in enumerate(payload))
            if opcode==8: raise EOFError()
            if opcode==9: self.frame(payload,10); continue
            if opcode==1: return json.loads(payload)
    def rpc(self,method,params):
        identity=self.next_id; self.next_id+=1; self.send({'id':identity,'method':method,'params':params})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            value=self.receive()
            if value.get('id')==identity and 'method' not in value:
                if 'error' in value:
                    self.errors.append({'method':method, 'syntheticError':value['error']})
                    return None
                return value['result']
            self.events.append(value)
        raise TimeoutError()
    def close(self):
        try: self.frame(struct.pack('!H',1000),8)
        except OSError: pass
        self.s.close()
report={'hostAuthRead':False,'providerContactTested':False,'toolInvocationTested':False,'authOwnerProven':False,'platformUidGatewayImplemented':False}
a=b=None
try:
    deadline=time.monotonic()+10
    while not Path(path).exists() and time.monotonic()<deadline:
        if p.poll() is not None: raise RuntimeError('fixture server startup failed')
        time.sleep(.05)
    a=Client('original'); b=Client('web')
    def tool(name): return {'type':'function','name':name,'description':'Synthetic registration only','inputSchema':{'type':'object','properties':{}}}
    first=a.rpc('thread/start',{'model':'gpt-6.1-sol','cwd':str(root/'original'),'approvalPolicy':'never','sandbox':'read-only','dynamicTools':[tool('original_fixture_tool')]})
    assert first
    original=first['thread']['id']
    assert a.rpc('thread/name/set',{'threadId':original,'name':'synthetic-original-private-sentinel'}) is not None
    peer=b.rpc('thread/read',{'threadId':original,'includeTurns':False})
    report['rawPeerForeignThreadReadAllowed']=bool(peer and peer['thread']['id']==original)
    report['rawPeerPrivateNameExposed']=bool(peer and peer['thread'].get('name')=='synthetic-original-private-sentinel')
    own=b.rpc('thread/start',{'model':'gpt-6.1-sol','cwd':str(root/'web'),'approvalPolicy':'never','sandbox':'read-only','dynamicTools':[tool('web_fixture_tool')]})
    assert own
    web=own['thread']['id']
    assert b.rpc('thread/name/set',{'threadId':web,'name':'synthetic-web-persisted'}) is not None
    registrations={}
    for file in (root/'codex'/'sessions').rglob('*.jsonl'):
        for line in file.read_text().splitlines():
            value=json.loads(line)
            if value.get('type')=='session_meta':
                data=value['payload']
                registrations[data.get('id')]=[v['name'] for v in data.get('dynamic_tools',[])]
    for file in (root/'codex').glob('state_*.sqlite'):
        with sqlite3.connect('file:'+str(file)+'?mode=ro',uri=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_dynamic_tools'").fetchone():
                for identity in (original,web):
                    rows=connection.execute('SELECT name FROM thread_dynamic_tools WHERE thread_id=? ORDER BY position',(identity,)).fetchall()
                    if rows: registrations[identity]=[row[0] for row in rows]
    report['separateToolRegistrationRequestsAccepted']=True
    report['toolRegistrationPersistenceObserved']=registrations.get(original)==['original_fixture_tool'] and registrations.get(web)==['web_fixture_tool']
    first_web_events=list(b.events); b.close(); b=None
    assert a.rpc('thread/read',{'threadId':original,'includeTurns':False})
    report['originalWorksAfterWebDisconnect']=True
    b=Client('web_reconnect')
    resumed=b.rpc('thread/resume',{'threadId':web,'model':'gpt-6.1-sol'})
    report['webSameThreadReconnect']=bool(resumed and resumed['thread']['id']==web)
    report['syntheticResumeErrors']=list(b.errors)
    a.close(); a=None
    assert p.poll() is None and b.rpc('model/list',{'includeHidden':False})
    report['serverAndWebSurviveOriginalDisconnect']=True
    b.close(); b=None
    time.sleep(.15)
    report['serverAliveAfterBothDisconnect']=p.poll() is None
    assert report['serverAliveAfterBothDisconnect']
    report['webObservedForeignThreadEvents']=any(v.get('method','').startswith('thread/') and (v.get('params',{}).get('threadId')==original or v.get('params',{}).get('thread',{}).get('id')==original) for v in first_web_events)
finally:
    if a: a.close()
    if b: b.close()
    p.terminate()
    try: p.wait(timeout=10)
    except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=2)
    report['ownedFixtureServerExitCode']=p.returncode
    print(json.dumps(report))
assert report['rawPeerForeignThreadReadAllowed'] and report['rawPeerPrivateNameExposed']
assert report['originalWorksAfterWebDisconnect'] and report['webSameThreadReconnect']
assert report['serverAndWebSurviveOriginalDisconnect'] and report['serverAliveAfterBothDisconnect']
assert report['ownedFixtureServerExitCode']==0
