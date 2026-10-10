"""Fixture-only Unix WebSocket transport copied from the approved stock probe."""
import base64, hashlib, json, os, socket, struct, time
class Client:
    def __init__(self,label,path):
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
