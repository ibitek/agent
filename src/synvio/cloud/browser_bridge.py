"""Trusted bridge copied into a VM. Never imported/executed on the API host."""
import base64
import hmac
import ipaddress
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


def public_url(value):
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password:
        raise ValueError('Only public HTTP(S) addresses without credentials are supported.')
    if parsed.port not in {None,80,443}:
        raise ValueError('Only standard web ports are supported.')
    if host in {'localhost','metadata.google.internal'} or host.endswith(('.localhost','.local','.internal','.localdomain')):
        raise ValueError('Private network addresses are blocked.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host:
            raise ValueError('Use a public domain.') from None
    else:
        if not address.is_global:
            raise ValueError('Private network addresses are blocked.')
    return value


def main():
    from playwright.sync_api import sync_playwright
    secret = os.environ['BROWSER_CONTROL_TOKEN']
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width':1280,'height':720}, accept_downloads=False, service_workers='block')
        console, pointer = [], [None]
        def allow_request(route):
            try:
                public_url(route.request.url)
            except ValueError:
                route.abort()
            else:
                route.continue_()
        context.route('**/*',allow_request)
        def log(level,text):
            console.append({'level':level,'text':text[:800]})
            del console[:-25]
        def watch(page):
            if len(context.pages)>6:
                page.close(); return
            page.on('console',lambda message:log(message.type,message.text))
            page.on('pageerror',lambda error:log('error',str(error)))
            page.on('dialog',lambda dialog:dialog.dismiss())
        context.on('page',watch)
        context.new_page()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+secret):
                    self.send_error(403); return
                size = int(self.headers.get('Content-Length','0'))
                if self.headers.get('Content-Type') != 'application/json' or not 0 < size <= 16000:
                    self.send_error(400); return
                try:
                    body = json.loads(self.rfile.read(size))
                    pages = context.pages
                    if not pages:
                        pages = [context.new_page()]
                    index = min(max(int(body.get('tab',0)),0),len(pages)-1)
                    page = pages[index]
                    kind = body['operation']
                    if kind == 'navigate':
                        page.goto(public_url(body['url']),wait_until='domcontentloaded',timeout=12000)
                    elif kind == 'new_tab':
                        if len(pages)>=6:
                            raise ValueError('Six browser tabs are allowed.')
                        page = context.new_page(); index = len(context.pages)-1
                    elif kind == 'close_tab':
                        page.close()
                        if not context.pages: context.new_page()
                        index = min(index,len(context.pages)-1); page = context.pages[index]
                    elif kind in {'click','move'}:
                        x,y=float(body['x']),float(body['y'])
                        if not 0 <= x < 1280 or not 0 <= y < 720: raise ValueError('Invalid pointer location.')
                        page.mouse.move(x,y); pointer[0]={'x':x,'y':y}
                        if kind=='click': page.mouse.click(x,y)
                    elif kind == 'type':
                        page.keyboard.insert_text(body.get('text','')[:2000])
                    elif kind == 'key':
                        key = body['key']
                        if key not in {'Enter','Tab','Escape','Backspace','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'}:
                            raise ValueError('Unsupported key.')
                        page.keyboard.press(key)
                    elif kind == 'scroll':
                        page.mouse.wheel(0,max(-1500,min(1500,int(body.get('delta',0)))))
                    elif kind not in {'capture','inspect','select_tab'}:
                        raise ValueError('Unsupported browser operation.')
                    screenshot = page.screenshot(type='jpeg',quality=45,timeout=5000)
                    if len(screenshot)>650000:
                        raise ValueError('The browser image exceeds the transfer limit.')
                    result = {'url':page.url,'title':page.title()[:180], 'active_tab':index,
                              'tabs':[{'index':i,'title':p.title()[:80],'url':p.url[:4096]} for i,p in enumerate(context.pages)],
                              'screenshot':'data:image/jpeg;base64,'+base64.b64encode(screenshot).decode(),
                              'captured_at':time.time(),'pointer':pointer[0],'console':console[-25:]}
                    if kind=='inspect':
                        result['page_text']=page.locator('body').inner_text(timeout=2000)[:12000]
                        result['links']=page.locator('a[href]').evaluate_all("nodes => nodes.slice(0,40).map(node => ({text:node.innerText.slice(0,100),url:node.href}))")
                    del console[:-25]
                    data=json.dumps(result).encode()
                    self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                except Exception as exc:
                    data=json.dumps({'error':str(exc)[:500]}).encode()
                    self.send_response(400); self.end_headers(); self.wfile.write(data)
        HTTPServer(('127.0.0.1',8765),Handler).serve_forever()


if __name__=='__main__':
    main()
