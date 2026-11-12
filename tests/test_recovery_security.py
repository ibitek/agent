from click.testing import CliRunner
from fastapi import HTTPException
import pytest
from synvio import web, cli as cli_module

@pytest.mark.parametrize('backend,trusted,status', [('docker','1',503),('host','0',403),('unknown','1',422)])
def test_backend_refuses_unsafe_execution(monkeypatch,backend,trusted,status):
    monkeypatch.setenv('SYNVIO_EXECUTION_BACKEND',backend)
    monkeypatch.setenv('SYNVIO_TRUST_HOST_TEST_EXECUTION',trusted)
    with pytest.raises(HTTPException) as error:
        web._require_execution_backend()
    assert error.value.status_code==status

def test_cli_docker_never_opens_engine(monkeypatch,tmp_path):
    def forbidden(*args,**kwargs):
        pytest.fail('Engine must not open for Docker request')
    monkeypatch.setattr(cli_module,'engine_for',forbidden)
    result=CliRunner().invoke(cli_module.cli,['dev','fix','--workspace',str(tmp_path),'--task','Fix tests','--execution-backend','docker','--docker-image','image@sha256:'+'a'*64,'--trusted-host-execution'])
    assert result.exit_code==2
    assert 'no host fallback' in result.output

def test_cli_untrusted_host_never_opens_engine(monkeypatch,tmp_path):
    def forbidden(*args,**kwargs):
        pytest.fail('Engine must not open without host opt-in')
    monkeypatch.setattr(cli_module,'engine_for',forbidden)
    result=CliRunner().invoke(cli_module.cli,['dev','fix','--workspace',str(tmp_path),'--task','Fix tests','--execution-backend','host'])
    assert result.exit_code==1
    assert 'not sandboxed' in result.output
