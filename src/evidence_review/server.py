"""Authenticated loopback-only service; serves packaged assets and neutral bundles."""
from __future__ import annotations
import json
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from .contracts import validate_bundle
from .store import Conflict
from .hooks import Hooks, run_hook

MAX_BODY=2_000_000
ASSETS={'/':('index.html','text/html'),'/app.js':('app.js','application/javascript'),'/styles.css':('styles.css','text/css')}


@dataclass
class ReviewHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    token: str
    origin: str

    @property
    def url(self):return self.origin+'/#token='+self.token
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def open_review(bundle,store,hooks=None,launch=True,port=0):
    bundle=validate_bundle(bundle.model_dump(mode='json') if hasattr(bundle,'model_dump') else bundle)
    store.register(bundle);hooks=hooks or Hooks();token=secrets.token_urlsafe(32)
    current=[bundle];mutex=threading.RLock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass # No content/token in server logs.
        def response(self,code,body,mime='application/json'):
            data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode() if mime=='application/json' else body
            self.send_response(code)
            self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(data)
        def authorized(self,post=False):
            if self.headers.get('Host')!=self.server.authority:return False
            if post and self.headers.get('Origin')!=self.server.origin:return False
            supplied=self.headers.get('Authorization','')
            return secrets.compare_digest(supplied,'Bearer '+token)
        def do_GET(self):
            if self.headers.get('Host')!=self.server.authority:
                self.response(403,{'error':'invalid host'});return
            if self.path in ASSETS:
                name,mime=ASSETS[self.path];self.response(200,files('evidence_review').joinpath('ui',name).read_bytes(),mime);return
            if not self.authorized():self.response(403,{'error':'authorization required'});return
            with mutex:
                if self.path=='/api/bundle':self.response(200,current[0].model_dump(mode='json'))
                elif self.path=='/api/state':self.response(200,store.load_task(current[0].bundle_id))
                else:self.response(404,{'error':'unknown path'})
        def do_POST(self):
            if not self.authorized(post=True):self.response(403,{'error':'invalid authorization or origin'});return
            if self.path not in ('/api/save','/api/submit','/api/reconcile','/api/next'):
                self.response(404,{'error':'unknown path'});return
            try:
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('JSON required')
                length=int(self.headers.get('Content-Length','-1'))
                if length<0 or length>MAX_BODY:raise ValueError('invalid body size')
                body=json.loads(self.rfile.read(length))
                with mutex:
                    b=current[0]
                    if body.get('bundle_id')!=b.bundle_id or body.get('bundle_hash')!=b.bundle_hash:raise Conflict('bundle changed')
                    if self.path=='/api/next':
                        state=store.load_task(b.bundle_id)
                        if state['last_submission'] != state['revision'] or state['hook']['status']!='succeeded':raise Conflict('submission/continuation incomplete')
                        nxt=hooks.next_bundle() if hooks.next_bundle else None
                        if nxt is None:self.response(200,{'done':True});return
                        nxt=validate_bundle(nxt.model_dump(mode='json'));store.register(nxt);current[0]=nxt
                        self.response(200,{'bundle':nxt.model_dump(mode='json')});return
                    if self.path=='/api/reconcile':
                        state=store.load_task(b.bundle_id)
                        if state['last_submission'] is None:raise ValueError('nothing submitted')
                        state=run_hook(store,b.bundle_id,state['last_submission'],hooks,reconcile=True)
                    else:
                        allowed={'bundle_id','bundle_hash','revision','key','answers','assessor','active_seconds','amendment_reason'}
                        if set(body)-allowed:raise ValueError('unknown request field')
                        fn=store.save_submission if self.path=='/api/submit' else store.save_snapshot
                        kwargs={'amendment_reason':body.get('amendment_reason','')} if self.path=='/api/submit' else {}
                        state=fn(b,body['revision'],body['key'],body['answers'],body['assessor'],body.get('active_seconds',0),**kwargs)
                        if self.path=='/api/submit':state=run_hook(store,b.bundle_id,state['last_submission'],hooks)
                    self.response(200,state)
            except Conflict as exc:self.response(409,{'error':str(exc)})
            except (ValueError,KeyError,TypeError) as exc:self.response(400,{'error':str(exc)})
            except Exception as exc:
                # Durable state remains recoverable. Storage/hook failures are visible, never acknowledged as saved.
                self.response(500,{'error':f'{type(exc).__name__}: {exc}'})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.authority=f'127.0.0.1:{server.server_port}';server.origin='http://'+server.authority
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    handle=ReviewHandle(server,thread,token,server.origin)
    if launch:webbrowser.open(handle.url)
    return handle
