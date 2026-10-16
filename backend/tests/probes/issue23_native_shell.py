# Run inside the pinned runner image with --network none, never on the host.
# Synthetic local provider only; no credentials or paid completions.
import json,os,queue,subprocess,threading
from pathlib import Path
from runner.shell_broker import ShellBroker
broker=ShellBroker(Path('/tmp/native-broker.sock'),lambda turn,args: turn['owner'],lambda value:value,wall=1)
from http.server import BaseHTTPRequestHandler,HTTPServer
os.makedirs('/tmp/codex',exist_ok=True)
counts={}; observations=[]
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  key=body['client_metadata']['turn_id']; counts[key]=counts.get(key,0)+1
  if counts[key]==1:
   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
   item={'type':'custom_tool_call','id':'tool_'+key,'call_id':'call_'+key,'name':'exec','namespace':'functions','input':'const names=ALL_TOOLS.filter(x=>x.name.includes("shell")).map(x=>x.name); text(names); if(names.length) text(await tools.mcp__shell__shell({command:"printf native-bridge-ok"}));'}
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
args+=['-c','mcp_servers.shell.tools.shell.approval_mode="approve"','-c','mcp_servers.shell.enabled_tools=["shell"]','-c','mcp_servers.shell.tool_timeout_sec=325']
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
  lease=broker.activate({'owner':mode=='live'})
  params['config']['mcp_servers.shell.env.SHELL_BROKER_SOCKET']='/tmp/native-broker.sock'
  params['config']['mcp_servers.shell.env.SHELL_BRIDGE_LEASE']=lease
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
  result=observations[-1];print(mode,json.dumps(result),flush=True)
  catalogs=[part['text'] for part in result if part.get('text','').startswith('[')]
  assert json.loads(catalogs[0]) == (["mcp__shell__shell"] if mode == "live" else [])
  if mode=='live':
   assert 'native-bridge-ok' in json.dumps(result[1:]) and 'completed' in json.dumps(result[1:])
   history=rpc(800+n,'thread/read',{'threadId':tid,'includeTurns':True})['thread']
   calls=[item for turn in history['turns'] for item in turn['items'] if item['type']=='mcpToolCall']
   assert calls, history
   from app.conversations import display_native_item
   projected=display_native_item(calls[-1])[0]
   assert projected['status']=='completed' and projected['output']['stdout']=='native-bridge-ok', projected
finally:
 p.kill();p.wait(timeout=5);server.shutdown();broker.close()
