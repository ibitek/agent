"""Authenticated short-lived browser VMs, with no public ports or host secrets."""
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .browser_bridge import public_url
from .common import public_error
from .store import Store

router=APIRouter(prefix='/api/browser')
PYTHON='/vercel/sandbox/browser-env/bin/python'
BRIDGE='/vercel/sandbox/browser_bridge.py'
LIFETIME=300
REQUEST='''import json,sys,urllib.request
data=json.load(sys.stdin)
req=urllib.request.Request('http://127.0.0.1:8765',data=json.dumps(data['action']).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+data['token']})
with urllib.request.urlopen(req,timeout=20) as response:
    raw=response.read(1100001)
    if len(raw)>1100000: raise ValueError('Browser response exceeded limit')
    print(raw.decode())
'''


class Start(BaseModel):
    request_id:str=Field(pattern=r'^[a-f0-9]{32}$')


class Action(Start):
    operation:Literal['capture','inspect','navigate','new_tab','close_tab','select_tab','click','move','scroll','type','key']
    tab:int=Field(default=0,ge=0,le=5)
    url:str|None=Field(default=None,max_length=4096)
    x:float=Field(default=0,ge=0,lt=1280,allow_inf_nan=False)
    y:float=Field(default=0,ge=0,lt=720,allow_inf_nan=False)
    text:str=Field(default='',max_length=2000)
    key:Literal['Enter','Tab','Escape','Backspace','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End']='Enter'
    delta:int=Field(default=0,ge=-1500,le=1500)


def state(store, value):
    if not re.fullmatch(r'[a-f0-9]{32}',value): raise HTTPException(404,'Browser session not found.')
    item=store.get_json('browser-session',value)
    if not item: raise HTTPException(404,'Browser session expired.')
    if item['status'] in {'starting','running'} and time.time()>=item['deadline']:
        item.update(status='expired',control_token=None)
        store.put_json('browser-session',value,item,expiry=600)
    return item


def public_state(item):
    return {key:item.get(key) for key in ('id','status','deadline','started_at','error')}


def image(store):
    item=store.get_json('settings','browser-image')
    return item if item and item.get('expires_at',0)>time.time()+60 else None


@router.get('/status')
def status():
    store=Store()
    current=store.get_json('browser-current','owner')
    try: session=public_state(state(store,current)) if current else None
    except HTTPException: session=None
    return {'prepared':bool(image(store)),'session':session,'max_seconds':LIFETIME,'compute_per_minute_usd':'0.005','agent_control':'read_only'}


@router.post('/prepare')
async def prepare():
    from .app import tasks_ready
    if not tasks_ready('app'): raise HTTPException(503,'Cloud environments are disabled.')
    from vercel import sandbox
    from vercel.sandbox import NetworkPolicy,SandboxResources
    store=Store()
    if image(store): return {'prepared':True,'cached':True}
    lock=store.key('browser-prepare-lock')
    if not store.call('SET',lock,'1','NX','EX',270): raise HTTPException(409,'Browser preparation is in progress.')
    box=None
    try:
        day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if not store.meter('browser-prepare:'+day,2,172800): raise HTTPException(429,'The two-preparation daily allowance has been reached.')
        box=await sandbox.create_sandbox(persistent=False,execution_time_limit=240,resources=SandboxResources(vcpus=1,memory=2048),ports=[],env={},network_policy=NetworkPolicy.allow_all(),tags={'app':'synvio','purpose':'browser-image'})
        await box.run_command('uv',['venv','--python','python3','/vercel/sandbox/browser-env'],capture_output=True,check=True,kill_after=20)
        await box.run_command('uv',['pip','install','--python',PYTHON,'playwright==1.65.1'],capture_output=True,check=True,kill_after=45)
        await box.run_command(PYTHON,['-m','playwright','install-deps','chromium'],sudo=True,capture_output=True,check=True,kill_after=70)
        await box.run_command(PYTHON,['-m','playwright','install','--only-shell','chromium'],capture_output=True,check=True,kill_after=70)
        await box.update(network_policy=NetworkPolicy.deny_all())
        await box.run_command(PYTHON,['-c','from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(headless=True)\n b.close()'],capture_output=True,check=True,kill_after=15)
        snapshot=await box.snapshot(expiration=86400)
        await box.stop()
        store.put_json('settings','browser-image',{'snapshot_id':snapshot.id,'producer_name':box.name,'expires_at':time.time()+86400},expiry=86400)
        return {'prepared':True,'cached':False}
    except HTTPException: raise
    except Exception as exc:
        if box is not None: await box.destroy()
        raise HTTPException(503,public_error(exc)) from None
    finally:
        store.call('DEL',lock)


@router.post('/sessions')
async def start(body:Start):
    from .app import tasks_ready
    if not tasks_ready('app'): raise HTTPException(503,'Cloud environments are disabled.')
    from vercel import sandbox
    from vercel.sandbox import NetworkPolicy,NetworkPolicySubnets,SandboxResources,SnapshotSource
    store=Store()
    cached=store.get_json('browser-start',body.request_id)
    if cached: return public_state(state(store,cached))
    if not store.call('SET',store.key('browser-start-lock'),body.request_id,'NX','EX',90): raise HTTPException(409,'A browser is starting.')
    box=None; item=None
    try:
        current=store.get_json('browser-current','owner')
        if current:
            try: prior=state(store,current)
            except HTTPException: prior={'status':'expired'}
            if prior['status'] in {'starting','running'}: return public_state(prior)
        prepared=image(store)
        if not prepared: raise HTTPException(409,'Prepare the browser image first.')
        day=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if not store.meter('browser-start:'+day,4,172800): raise HTTPException(429,'The four-session daily allowance has been reached.')
        value=uuid.uuid4().hex
        item={'id':value,'status':'starting','started_at':time.time(),'deadline':time.time()+LIFETIME,'control_token':secrets.token_hex(32)}
        store.put_json('browser-session',value,item,expiry=900)
        store.put_json('browser-start',body.request_id,value,expiry=900)
        store.put_json('browser-current','owner',value,expiry=900)
        deny=('0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','198.18.0.0/15','224.0.0.0/4','240.0.0.0/4','::/128','::1/128','::ffff:0:0/96','fc00::/7','fe80::/10','ff00::/8')
        box=await sandbox.create_sandbox(source=SnapshotSource(snapshot_id=prepared['snapshot_id']),persistent=False,execution_time_limit=LIFETIME,resources=SandboxResources(vcpus=1,memory=2048),ports=[],env={},network_policy=NetworkPolicy.custom(allow={'*':()},subnets=NetworkPolicySubnets(deny=deny)),tags={'app':'synvio','purpose':'browser-session'})
        if store.get_json('browser-stop',value):
            await box.destroy(); box=None
            raise HTTPException(409,'Browser start was cancelled.')
        item['name']=box.name
        store.put_json('browser-session',value,item,expiry=900)
        await box.fs.write_text(BRIDGE,Path(__file__).with_name('browser_bridge.py').read_text(encoding='utf-8'))
        await box.create_command(PYTHON,[BRIDGE],env={'BROWSER_CONTROL_TOKEN':item['control_token']},kill_after=LIFETIME)
        probe="import socket,time\nfor i in range(24):\n try:\n  socket.create_connection(('127.0.0.1',8765),timeout=.2).close();break\n except OSError:time.sleep(.25)\nelse:raise RuntimeError('Browser did not start')"
        await box.run_command('python3',['-c',probe],capture_output=True,check=True,kill_after=10)
        item['status']='running'
        committed=store.call('EVAL',"local raw=redis.call('GET',KEYS[1]);if raw and cjson.decode(raw).status=='starting' and redis.call('EXISTS',KEYS[2])==0 then redis.call('SET',KEYS[1],ARGV[1],'EX',900);return 1 else return 0 end",2,store.key('browser-session',value),store.key('browser-stop',value),json.dumps(item))
        if not committed:
            await box.destroy(); box=None
            item.update(status='stopped',control_token=None)
            store.put_json('browser-session',value,item,expiry=900)
            raise HTTPException(409,'Browser start was cancelled.')
        return public_state(item)
    except HTTPException:
        if box is not None: await box.destroy()
        raise
    except Exception as exc:
        if box is not None: await box.destroy()
        error=public_error(exc).replace(item['control_token'],'[redacted]') if item else public_error(exc)
        if item:
            item.update(status='failed',error=error,control_token=None)
            store.put_json('browser-session',item['id'],item,expiry=900)
        raise HTTPException(503,error) from None
    finally:
        store.call('EVAL',"if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,store.key('browser-start-lock'),body.request_id)


async def perform(value, body, *, actor='user', allowed_urls=()):
    from vercel import sandbox
    store=Store(); item=state(store,value)
    if item['status']!='running': raise HTTPException(409,'This browser session is not running.')
    if actor=='agent' and body.operation not in {'inspect','capture','navigate','move','select_tab'}:
        raise HTTPException(403,'Agent input actions are not enabled. Use manual browser control.')
    if body.operation=='navigate':
        try: public_url(body.url or '')
        except ValueError as exc: raise HTTPException(422,str(exc)) from None
        if actor=='agent' and body.url not in {*allowed_urls,*(store.get_json('browser-links',value) or [])}:
            raise HTTPException(403,'The agent may open an exact URL supplied by you, or an observed link on the current site. Paste the new address to authorize it.')
    cached=store.get_json('browser-action',body.request_id)
    if cached:
        raise HTTPException(409,'This browser action was already submitted. It will not be repeated automatically.')
    lock=store.key('browser-action-lock',value)
    if not store.call('SET',lock,body.request_id,'NX','EX',35): raise HTTPException(409,'The browser is busy.')
    try:
        if body.operation not in {'capture','inspect'}:
            store.put_json('browser-action',body.request_id,True,expiry=900)
        box=await sandbox.get_sandbox(name=item['name'])
        # Pin to the current VM session: never lazily resume a stopped browser.
        session=box.current_session
        if session is None: raise HTTPException(409,'The browser has stopped.')
        from .sandbox_runner import BoundedOutput
        class Output(BoundedOutput):
            def write(self,text):
                if len(self.value)+len(text)>1100000: raise ValueError('Browser response exceeded limit.')
                self.value+=text; return len(text)
        output=Output()
        result=await session.run_command('python3',['-c',REQUEST],input=json.dumps({'token':item['control_token'],'action':body.model_dump(exclude={'request_id'})}),stdout=output,stderr=Output(),kill_after=25)
        if result.returncode!=0: raise ValueError('Browser action failed. Refresh the frame or check the address. The action was not retried.')
        frame=json.loads(output.value)
        if not isinstance(frame.get('screenshot'),str) or not frame['screenshot'].startswith('data:image/jpeg;base64,'): raise ValueError('Invalid browser image.')
        store.put_json('browser-last-event',value,{'operation':body.operation,'actor':actor,'captured_at':frame['captured_at']},expiry=900)
        latest=state(store,value)
        if latest['status']!='running': raise HTTPException(409,'The browser has stopped.')
        if body.operation=='inspect':
            origin=urlsplit(frame.get('url','')).netloc
            links=[link['url'] for link in frame.get('links',[]) if urlsplit(link.get('url','')).netloc==origin][:40]
            store.put_json('browser-links',value,links,expiry=900)
        if actor=='agent':
            store.put_json('browser-agent-event',value,{'operation':body.operation,'captured_at':frame['captured_at']},expiry=900)
        return {**frame,'session':public_state(item),'actor':actor,'operation':body.operation,'agent_event':store.get_json('browser-agent-event',value)}
    except HTTPException: raise
    except Exception as exc:
        raise HTTPException(503,public_error(exc).replace(item['control_token'],'[redacted]')) from None
    finally:
        store.call('EVAL',"if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,lock,body.request_id)


@router.post('/sessions/{value}/actions')
async def action(value:str,body:Action):
    return await perform(value,body)


@router.post('/sessions/{value}/stop')
async def stop(value:str):
    from vercel import sandbox
    store=Store(); item=state(store,value)
    store.put_json('browser-stop',value,True,expiry=900)
    if item['status'] in {'running','starting','stopping','expired'}:
        item['status']='stopping'; store.put_json('browser-session',value,item,expiry=900)
        if item.get('name'):
            box=await sandbox.get_sandbox(name=item['name'])
            await box.destroy()
        item.update(status='stopped',control_token=None)
        store.put_json('browser-session',value,item,expiry=900)
    return public_state(item)


@router.post('/image/remove')
async def remove_image():
    from vercel import sandbox
    store=Store(); prepared=store.get_json('settings','browser-image')
    if prepared:
        producer=await sandbox.get_sandbox(name=prepared['producer_name'])
        await producer.destroy(delete_orphan_snapshots=True)
        store.call('DEL',store.key('settings','browser-image'))
    return {'prepared':False}
