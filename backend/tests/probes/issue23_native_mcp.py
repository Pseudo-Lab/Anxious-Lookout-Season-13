# Run inside the pinned runner image with --network none, never on the host.
# Synthetic local provider only; no credentials or paid completions.
import json,os,queue,subprocess,threading
from http.server import BaseHTTPRequestHandler,HTTPServer
os.makedirs('/tmp/codex',exist_ok=True)
counts={}; observations=[]
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  key=body['client_metadata']['turn_id']; counts[key]=counts.get(key,0)+1
  if counts[key]==1:
   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
   item={'type':'custom_tool_call','id':'tool_'+key,'call_id':'call_'+key,'name':'exec','namespace':'functions','input':'text(ALL_TOOLS.filter(x=>x.name.includes("shell")).map(x=>x.name))'}
   for event in [{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':{'id':'response_'+key,'status':'completed','output':[item],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]:
    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
  else:
   outputs=[x for x in body['input'] if x.get('type')=='custom_tool_call_output']
   observations.append(outputs[-1]['output'] if outputs else [])
   self.send_response(400);self.end_headers();self.wfile.write(b'{"error":{"message":"offline probe complete"}}')
 def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',8765),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
args=['/usr/local/bin/codex','app-server','--listen','stdio://']
args+=['-c','mcp_servers.shell.command="/usr/local/bin/python"','-c','mcp_servers.shell.args=["-I","/stub.py"]','-c','mcp_servers.shell.enabled=false']
for c in ['web_search="disabled"','features.shell_tool=false','model_provider="probe"','model_providers.probe.name="OpenAI"','model_providers.probe.base_url="http://127.0.0.1:8765/v1"','model_providers.probe.wire_api="responses"','model_providers.probe.requires_openai_auth=false','model_providers.probe.request_max_retries=0','model_providers.probe.stream_max_retries=0']:
 args+=['-c',c]
# MCP configuration is trusted fixed process code, never model input.
p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
q=queue.Queue()
def read():
 for line in p.stdout:q.put(json.loads(line))
threading.Thread(target=read,daemon=True).start()
def rpc(i,m,params):
 p.stdin.write(json.dumps(dict(id=i,method=m,params=params))+'\n');p.stdin.flush()
 while True:
  r=q.get(timeout=20)
  if r.get('id')==i:
   if 'error' in r:raise RuntimeError(r)
   return r['result']
try:
 rpc(1,'initialize',{'clientInfo':{'name':'offline-probe','version':'1'},'capabilities':{'experimentalApi':True}})
 tid=None
 for n,mode in enumerate(['disabled','live','disabled','live']):
  params={'model':'gpt-6.1-sol','cwd':'/tmp','approvalPolicy':'never','sandbox':'read-only','config':{'web_search':mode,'features.shell_tool':False}}
  params['config']['mcp_servers.shell.enabled']=mode=='live'
  if tid:
   p.kill();p.wait(timeout=5);q=queue.Queue()
# MCP configuration is trusted fixed process code, never model input.
   p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
   threading.Thread(target=read,daemon=True).start()
   rpc(500+n,'initialize',{'clientInfo':{'name':'offline-probe','version':'1'},'capabilities':{'experimentalApi':True}})
   params['threadId']=tid
  else:params.update(historyMode='legacy',ephemeral=False)
  r=rpc(10+n*2,'thread/resume' if tid else 'thread/start',params)
  assert tid is None or tid == r['thread']['id']
  tid=r['thread']['id']
  rpc(11+n*2,'turn/start',{'threadId':tid,'input':[{'type':'text','text':'offline probe'}]})
  while True:
   msg=q.get(timeout=20)
   if msg.get('method')=='turn/completed':
    break
  result=observations[-1][-1]['text'];print(mode,result,flush=True)
  assert json.loads(result) == (["mcp__shell__shell"] if mode == "live" else [])
finally:
 p.kill();p.wait(timeout=5);server.shutdown()
