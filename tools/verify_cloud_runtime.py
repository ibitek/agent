"""Smoke-check the production dependency set without making provider requests."""
from pathlib import Path
import importlib.util
import os
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'src'))
# Reuse the established key removal and external-network denial.
import tools.verify_offline  # noqa: F401

if __name__=='__main__':
    for name in ('datasets','pandas','pyarrow','numpy'):
        assert importlib.util.find_spec(name) is None, name+' must not enter the lean runtime'
    from fastapi.testclient import TestClient
    from synvio.cloud.app import app
    from synvio.cloud import worker  # noqa: F401
    from synvio.config import SYNVIOConfig
    from synvio.runtime import engine_for,identify_engine
    with tempfile.TemporaryDirectory(prefix='synvio-runtime-check-') as temporary:
        os.environ['OPENJARVIS_HOME']=str(Path(temporary)/'sdk')
        with TestClient(app,base_url='https://runtime-check.test') as client:
            response=client.get('/api/session')
            assert response.status_code==200,response.text
        os.environ['OPENAI_API_KEY']='offline-placeholder-not-a-real-key'
        with engine_for(SYNVIOConfig(provider='openai',engine='cloud',model_override='gpt-4o-mini')) as engine:
            assert identify_engine(engine).is_cloud
        del os.environ['OPENAI_API_KEY']
    print('Lean runtime: session API and SDK cloud-engine setup passed; no provider calls.')
