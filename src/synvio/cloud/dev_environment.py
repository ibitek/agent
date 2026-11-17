"""Versioned Codex/browser toolchain snapshots. No inference or public ports."""
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from .common import public_error, validate_files
from .store import Store

router = APIRouter(prefix='/api/codex/environment')
VERSION = 'kaelia-dev-1'
CACHE = '/vercel/sandbox/starter-cache'
CODEX = '/vercel/sandbox/codex-runtime/node_modules/.bin/codex'
HELPER = '/vercel/sandbox/kaelia-tools/visual_review.py'


def starter_files():
    root = Path(__file__).with_name('starter')
    result = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(part in {'node_modules', 'dist', '.git'} for part in relative.parts) or not path.is_file(): continue
        result[relative.as_posix()] = path.read_text(encoding='utf-8')
    return validate_files(result)


def seed(task, files):
    if task.get('runtime') not in {'app', 'web'} or any(path.lower() not in {'readme.md', 'readme.txt'} for path in files):
        return dict(files), False
    if re.search(r'plain html|vanilla|without (?:react|framework|libraries)|bez (?:react|bibliotek)|czyst\w* html', task['task'], re.I):
        return dict(files), False
    return {**starter_files(), **files}, True


def prepared(store):
    item = store.get_json('settings', 'developer-image') or {}
    return item if item.get('version') == VERSION and item.get('expires_at', 0) > time.time() + 60 else None


@router.get('')
def status():
    item = prepared(Store())
    return {'prepared': bool(item), 'version': VERSION, 'expires_at': item.get('expires_at') if item else None,
            'tools': ['Codex CLI', 'Node.js', 'Python', 'Playwright Chromium', 'React / Tailwind / shadcn/ui starter'],
            'compute_per_minute_usd': '0.005', 'inference_started': False}


@router.post('/prepare')
async def prepare():
    from .app import tasks_ready
    if not tasks_ready('app'): raise HTTPException(503, 'Cloud execution is disabled.')
    store = Store()
    if prepared(store): return {'prepared': True, 'cached': True}
    if not store.call('SET', store.key('developer-prepare-lock'), '1', 'NX', 'EX', 290):
        raise HTTPException(409, 'The coding environment is already being prepared.')
    box = None
    try:
        if not store.meter('developer-prepare:' + datetime.now(timezone.utc).strftime('%Y-%m-%d'), 2, 172800):
            raise HTTPException(429, 'The daily coding environment preparation allowance was reached.')
        from .browser_sessions import prepare as prepare_browser, image
        from .codex_cli import VERSION as CODEX_VERSION
        from vercel import sandbox
        from vercel.sandbox import NetworkPolicy, SandboxResources, SnapshotSource
        await prepare_browser()
        browser = image(store)
        if not browser: raise ValueError('The browser image was not prepared.')
        box = await sandbox.create_sandbox(source=SnapshotSource(snapshot_id=browser['snapshot_id']), persistent=False,
            execution_time_limit=150, resources=SandboxResources(vcpus=1, memory=2048), ports=[], env={},
            network_policy=NetworkPolicy.custom(allow={'registry.npmjs.org': ()}), tags={'app': 'kaelia', 'purpose': 'developer-image'})
        await box.fs.mkdir(CACHE, recursive=True)
        async with box.fs.batch() as batch:
            for path, text in starter_files().items(): batch.write_text(CACHE + '/' + path, text)
        await box.fs.mkdir('/vercel/sandbox/kaelia-tools', recursive=True)
        await box.fs.write_text(HELPER, Path(__file__).with_name('visual_review.py').read_text(encoding='utf-8'))
        from .browser_sessions import PYTHON
        await box.run_command(PYTHON, ['-c', 'import playwright'], capture_output=True, check=True, kill_after=10)
        await box.run_command('node', ['-e', 'if(+process.versions.node.split(".")[0]<22)throw Error("Node 22 or newer required")'], capture_output=True, check=True, kill_after=10)
        await box.run_command('npm', ['install', '--prefix', '/vercel/sandbox/codex-runtime', '--ignore-scripts', '--no-audit', '--no-fund', '@openai/codex@' + CODEX_VERSION], env={}, capture_output=True, check=True, kill_after=55)
        await box.run_command(CODEX, ['--version'], env={}, capture_output=True, check=True, kill_after=10)
        await box.run_command('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=CACHE, env={}, capture_output=True, check=True, kill_after=55)
        await box.run_command('npm', ['run', 'build'], cwd=CACHE, env={}, capture_output=True, check=True, kill_after=25)
        await box.update(network_policy=NetworkPolicy.deny_all())
        await box.create_command('python3', ['-m', 'http.server', '4173', '--bind', '127.0.0.1', '--directory', CACHE + '/dist'], env={}, kill_after=35)
        probe = "import socket,time\nfor i in range(20):\n try:\n  socket.create_connection(('127.0.0.1',4173),timeout=.1).close();break\n except OSError:time.sleep(.1)\nelse:raise RuntimeError('Local preview did not start')"
        await box.run_command('python3', ['-c', probe], env={}, capture_output=True, check=True, kill_after=5)
        await box.run_command(PYTHON, [HELPER, '--url', 'http://127.0.0.1:4173', '--root', CACHE], env={}, capture_output=True, check=True, kill_after=25)
        snapshot = await box.snapshot(expiration=86400)
        await box.stop()
        store.put_json('settings', 'developer-image', {'snapshot_id': snapshot.id, 'version': VERSION,
            'expires_at': time.time() + 86400, 'producer_name': box.name, 'validation': 'built_and_rendered'}, expiry=86400)
        return {'prepared': True, 'cached': False}
    except HTTPException: raise
    except Exception as exc:
        if box is not None: await box.destroy()
        if isinstance(exc, subprocess.CalledProcessError):
            details = str(exc.stderr or '') + '\n' + str(exc.stdout or '')
            exc = RuntimeError(str(exc) + '\n' + details[-1600:])
        raise HTTPException(503, public_error(exc)) from None
    finally:
        store.call('DEL', store.key('developer-prepare-lock'))
