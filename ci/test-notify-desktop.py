"""Execute the compiled Windows program, native notification API and real HTTP client."""
import http.server,json,subprocess,sys,threading
from pathlib import Path
exe=str(Path(sys.argv[1]).resolve());calls=[]
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):self.process()
    def do_POST(self):self.process()
    def process(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.command=='POST' else None
        if self.path.endswith('/redeem'):
            assert payload=={'code':'simulation-one-time-code'}
            data={'token':'simulation-notification-only-token','full_name':'模拟审批人'}
        else:
            assert self.headers.get('Authorization')=='Bearer simulation-notification-only-token'
            if self.path.endswith('/poll'):data={'items':[{'id':27,'title':'申请审批通过','body':'【模拟数据】AR-CI-001','url':'#/approval/simulation'}],'full_name':'模拟审批人'}
            else:assert self.path.endswith('/ack') and payload=={'ids':[27]};data={'ok':True}
        calls.append(self.path)
        raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    subprocess.run([exe,'--http-test','http://127.0.0.1:'+str(server.server_port),'simulation-one-time-code'],check=True,timeout=45)
    assert calls==['/api/v1/notifications/desktop/redeem','/api/v1/notifications/desktop/poll','/api/v1/notifications/desktop/ack'],calls
    subprocess.run([exe,'--self-test',str(Path('notify-self-test.txt').resolve())],check=True,timeout=45)
    assert Path('notify-self-test.txt').read_text().startswith('PASS:')
    print('PASS compiled Windows EXE: pairing HTTP, Unicode payload, delivery acknowledgement, form, tray and native notification API')
    print('Actual popup visibility is not verified in the CI desktop; user Windows notification settings still apply.')
finally:server.shutdown()
