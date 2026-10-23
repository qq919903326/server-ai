"""HTTP protocol fixture; never connects to the developer's MCP/tools."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest


@pytest.fixture
def remote_mcp():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            if 'id' not in body:
                self.send_response(202)
                self.end_headers()
                return
            method = body['method']
            if method == 'initialize':
                result = {'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'HTTP test fixture','version':'1'}}
            elif method == 'tools/list':
                result = {'tools':[
                    {'name':'add','description':'Add numbers', 'inputSchema':{'type':'object','properties':{'a':{'type':'number'},'b':{'type':'number'}},'required':['a','b']}},
                    {'name':'other','description':'Not authorized', 'inputSchema':{'type':'object'}}]}
            elif method == 'tools/call':
                args = body['params']['arguments']
                result = {'content':[{'type':'text','text':str(args['a'] + args['b'])}], 'isError':False}
            else:
                result = {}
            encoded = json.dumps({'jsonrpc':'2.0','id':body['id'],'result':result}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def do_GET(self):
            self.send_response(405)
            self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        yield {'url':f'http://127.0.0.1:{server.server_port}/mcp','calls':calls}
    finally:
        server.shutdown()
        server.server_close()
