"""Owner-triggered Vite builds in disposable VMs; only text artifacts return."""
import json
import time

from .common import MAX_PROJECT_BYTES, public_error, validate_files
from .sandbox_runner import BoundedOutput
from .preview import build_preview

ROOT = '/vercel/sandbox/project'
COLLECT = '''import json
from pathlib import Path
root=Path('/vercel/sandbox/project/dist').resolve()
out={}
for p in root.rglob('*'):
    if p.is_symlink(): raise ValueError('Symlink artifacts are not supported')
    if not p.is_file(): continue
    if p.suffix not in {'.html','.js','.mjs','.css','.svg','.json','.txt'}: continue
    if not p.resolve().is_relative_to(root): raise ValueError('Artifact escaped dist')
    if p.stat().st_size>262144: raise ValueError('Artifact exceeds 256 KiB')
    out['dist/'+p.relative_to(root).as_posix()]=p.read_text(encoding='utf-8')
    if len(out)>100 or len(json.dumps(out).encode())>2097152: raise ValueError('Artifacts exceed preview limits')
Path('/vercel/sandbox/preview-artifacts.json').write_text(json.dumps(out),encoding='utf-8')
'''


def validate_app(files):
    files = validate_files(files)
    try:
        package = json.loads(files.get('package.json', ''))
        deps = {**package.get('dependencies', {}), **package.get('devDependencies', {})}
        if 'vite' not in deps or not isinstance(package.get('scripts', {}).get('build'), str):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('This preview supports Vite projects with package.json and a build script.') from None
    return files


async def build(store, task_id, request_id, files):
    from vercel import sandbox
    from vercel.sandbox import NetworkPolicy, SandboxResources
    files = validate_app(files)
    started = time.monotonic()
    def event(phase, **values):
        store.event(task_id, {'event': 'application_build', 'phase': phase, **values})
    try:
        event('starting')
        # No source code or credentials from the SYNVIO host enter this VM.
        async with sandbox.create_sandbox(persistent=False, execution_time_limit=160,
                resources=SandboxResources(vcpus=1, memory=2048), ports=[], env={},
                network_policy=NetworkPolicy.custom(allow={'registry.npmjs.org': ()}),
                tags={'app':'synvio','purpose':'build-preview'}) as box:
            await box.fs.mkdir(ROOT, recursive=True)
            async with box.fs.batch() as batch:
                for path, content in files.items():
                    batch.write_text(ROOT + '/' + path, content)
            event('installing')
            output = BoundedOutput()
            install = await box.run_command('npm', ['install','--ignore-scripts','--no-audit','--no-fund','--registry=https://registry.npmjs.org'], cwd=ROOT,
                env={'NPM_CONFIG_USERCONFIG':'/dev/null','NPM_CONFIG_IGNORE_SCRIPTS':'true'}, kill_after=70, stdout=output, stderr=output)
            event('install_finished', returncode=install.returncode, output=output.value)
            if install.returncode != 0:
                raise RuntimeError('Dependency installation failed. See Activity for the actual output.')
            await box.update(network_policy=NetworkPolicy.deny_all())
            event('building')
            output = BoundedOutput()
            result = await box.run_command('npm', ['run','build','--','--base=./'], cwd=ROOT, env={}, kill_after=50, stdout=output, stderr=output)
            event('build_finished', returncode=result.returncode, output=output.value)
            if result.returncode != 0:
                raise RuntimeError('Application build failed. See Activity for the actual output.')
            await box.run_command('python3', ['-c', COLLECT], cwd=ROOT, capture_output=True, check=True, kill_after=10)
            async with box.fs.open('/vercel/sandbox/preview-artifacts.json', 'rb') as reader:
                raw = await reader.read(MAX_PROJECT_BYTES + 1)
            if len(raw) > MAX_PROJECT_BYTES:
                raise ValueError('Build artifacts exceed the preview size limit.')
            artifacts = validate_files(json.loads(raw))
            if any(not path.startswith('dist/') for path in artifacts):
                raise ValueError('Invalid build artifact path.')
            preview = build_preview(artifacts)
            if not preview['available']:
                raise ValueError(preview['reason'])
            store.put_json('built-files', task_id, artifacts)
        # Context exit stops and destroys the VM before reporting success.
        state = {'status':'ready', 'request_id':request_id, 'duration_seconds':time.monotonic()-started,
                 'browser_verified':False, 'checks':'production_build', 'model_calls':0}
        store.put_json('application-build', task_id, state)
        store.event(task_id, {'event':'preview_updated','kind':'built_application'})
        event('ready')
        return state
    except Exception as exc:
        state = {'status':'failed','request_id':request_id,'error':public_error(exc),'model_calls':0}
        store.put_json('application-build', task_id, state)
        event('failed', error=state['error'])
        return state
