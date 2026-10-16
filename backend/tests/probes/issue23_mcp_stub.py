import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m:continue
 method=m.get('method')
 if method=='initialize':r={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'shell-probe','version':'1'}}
 elif method=='tools/list':r={'tools':[{'name':'shell','description':'Isolated shell probe','inputSchema':{'type':'object','properties':{'command':{'type':'string'}},'required':['command'],'additionalProperties':False}}]}
 elif method=='tools/call':r={'content':[{'type':'text','text':'offline probe only'}]}
 else:r={}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
