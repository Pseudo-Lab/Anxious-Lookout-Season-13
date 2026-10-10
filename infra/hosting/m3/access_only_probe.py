"""Docker-only native refusal/counterexamples; no production access-only mode."""
import argparse
import base64
import json
import os
import queue
import subprocess
import ssl
import ipaddress
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--default-retries',action='store_true')
parser.add_argument('--expired-token',action='store_true')
options=parser.parse_args()
root=Path('/tmp/native-access-only'); root.mkdir(mode=0o700)
home=root/'home'; codex=root/'codex'; work=root/'work'
for d in (home,codex,work): d.mkdir(mode=0o700)
models=json.loads(Path('/checks/models.json').read_text())
audit={'callbacksRefused':0,'bootstrapGets':0,'responsePosts':0,'postsAfterRefusal':0,'wrongModel':False,'managedRefreshPosts':0}
class Mock(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        audit['bootstrapGets']+=1
        if 'accounts/check' in self.path:
            body={'accounts':[{'id':'synthetic-account','plan_type':'plus','workspace_backend_origin':origin,'account_routing_override':'NO_CONSTRAINT'}], 'account_ordering':['synthetic-account'],'default_account_id':'synthetic-account'}
        elif 'models' in self.path: body=models
        else: body={}
        payload=json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def do_POST(self):
        data=self.rfile.read(int(self.headers.get('Content-Length','0')))
        if 'responses' in self.path:
            audit['responsePosts']+=1
            if audit['callbacksRefused']: audit['postsAfterRefusal']+=1
            try: audit['wrongModel'] |= json.loads(data).get('model')!='gpt-6.1-sol'
            except ValueError: audit['wrongModel']=True
        if 'oauth' in self.path or 'token' in self.path: audit['managedRefreshPosts']+=1
        payload=b'{"error":{"message":"synthetic unauthorized","type":"invalid_request_error","code":"invalid_api_key"}}'
        self.send_response(401); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'synthetic-loopback')])
cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
      .not_valid_after(datetime.now(timezone.utc)+timedelta(minutes=20))
      .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
      .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).sign(key,hashes.SHA256()))
cert_path=root/'ca.pem'; key_path=root/'key.pem'
cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
key_path.chmod(0o600)
server=ThreadingHTTPServer(('127.0.0.1',0),Mock)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert_path,key_path)
server.socket=context.wrap_socket(server.socket,server_side=True)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin='https://127.0.0.1:'+str(server.server_port)
args=['/usr/local/bin/codex','app-server','--listen','stdio://']
for value in ['cli_auth_credentials_store="ephemeral"','features.daemon_auto_start=false','features.code_mode_host=false',
              'features.shell_tool=false','features.unified_exec=false','features.multi_agent=false','features.multi_agent_v2=false',
              'features.apps=false','features.plugins=false','features.hooks=false','features.browser_use=false','features.computer_use=false',
              'features.unbounded_connection_retries=false','analytics.enabled=false','otel.exporter="none"','otel.trace_exporter="none"','otel.metrics_exporter="none"',
              'model="gpt-6.1-sol"','model_provider="fixture_openai"','forced_login_method="chatgpt"','chatgpt_base_url='+json.dumps(origin+'/backend-api'),
              'model_providers.fixture_openai.name="OpenAI synthetic fixture"','model_providers.fixture_openai.wire_api="responses"','model_providers.fixture_openai.requires_openai_auth=true','model_providers.fixture_openai.base_url='+json.dumps(origin+'/backend-api/codex'),'model_providers.fixture_openai.supports_websockets=false']:
    args+=['-c',value]
if not options.default_retries:
    for value in ['model_providers.fixture_openai.request_max_retries=0', 'model_providers.fixture_openai.stream_max_retries=0']:
        args+=['-c',value]
p=subprocess.Popen(args,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(codex),'LANG':'C.UTF-8','CODEX_CA_CERTIFICATE':str(cert_path)},
                   cwd=work,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
inbox=queue.Queue(); lock=threading.Lock()
def send(v):
    with lock: p.stdin.write(json.dumps(v)+'\n'); p.stdin.flush()
def reader():
    for line in p.stdout:
        try: v=json.loads(line)
        except ValueError: continue
        if 'id' in v and 'method' in v:
            if v['method']=='account/chatgptAuthTokens/refresh': audit['callbacksRefused']+=1
            send({'id':v['id'],'error':{'code':-32001,'message':'Access-only trial cannot refresh'}})
        else: inbox.put(v)
threading.Thread(target=reader,daemon=True).start()
def rpc(i,m,params):
    send({'id':i,'method':m,'params':params}); deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        v=inbox.get(timeout=max(.01,deadline-time.monotonic()))
        if v.get('id')==i:
            if 'error' in v: raise ValueError('RPC refused at '+m)
            return v['result']
    raise ValueError('RPC timeout')
stage='initialize'; completed=False; force=False
try:
    rpc(1,'initialize',{'clientInfo':{'name':'synthetic_access_only','version':'1'},'capabilities':{'experimentalApi':True}})
    send({'method':'initialized'})
    def seg(v): return base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=')
    token=seg({'alg':'none'})+'.'+seg({'email':'synthetic@example.invalid','exp':1 if options.expired_token else int(time.time())+3600,
          'https://api.openai.com/auth':{'chatgpt_account_id':'synthetic-account','chatgpt_plan_type':'plus'}})+'.synthetic'
    stage='external-login'; rpc(2,'account/login/start',{'type':'chatgptAuthTokens','accessToken':token,'chatgptAccountId':'synthetic-account','chatgptPlanType':'plus'})
    audit['expiredSyntheticLoginAccepted']=options.expired_token
    stage='account-routing'; rpc(3,'account/read',{'refreshToken':False})
    stage='thread-start'; t=rpc(4,'thread/start',{'model':'gpt-6.1-sol','cwd':str(work),'approvalPolicy':'never','sandbox':'read-only','dynamicTools':[]})['thread']['id']
    stage='turn-start'; rpc(5,'turn/start',{'threadId':t,'model':'gpt-6.1-sol','input':[{'type':'text','text':'Synthetic unauthorized test, no tools.'}]})
    stage='failed-turn'; deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        v=inbox.get(timeout=max(.01,deadline-time.monotonic()))
        if v.get('method')=='turn/completed':
            completed=v.get('params',{}).get('turn',{}).get('status')=='failed'; break
except Exception as e:
    print(json.dumps({'failureStage':stage,'failureType':type(e).__name__}))
finally:
    started=time.monotonic(); p.stdin.close()
    try: p.wait(timeout=50)
    except subprocess.TimeoutExpired: force=True; p.kill(); p.wait(timeout=2)
    server.shutdown()
    report={**audit,'failedTurnObserved':completed,'nativeExitCode':p.returncode,'forceKilled':force,
            'shutdownSeconds':round(time.monotonic()-started,2),'authJsonPersisted':(codex/'auth.json').exists(),
            'hostAuthRead':False,'realProviderContact':False,'platformBudgetTested':False,
            'applicationExpiryGuardTested':False,'defaultRetryCounterexample':options.default_retries}
    print(json.dumps(report))
retry_result=(audit['responsePosts']>1 and audit['postsAfterRefusal']>0) if options.default_retries else (audit['callbacksRefused']==1 and audit['responsePosts']==1 and not audit['postsAfterRefusal'])
if not (completed and retry_result and not audit['wrongModel'] and not audit['managedRefreshPosts'] and not (codex/'auth.json').exists() and p.returncode==0):
    raise SystemExit(1)
