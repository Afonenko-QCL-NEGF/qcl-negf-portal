"""Authenticated transport controls with an injected no-process adapter."""
import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings

TOKEN = '0123456789abcdef' * 4
CODE = '10dd2a4c-031b-425c-90b8-2f7ea564d313'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
RAW = b'{ "mode":"yaml", "template_id":"one", "catalog_source_id":"source", "yaml":"x" }'
PLAN = '{ "n": 9007199254740993, "x": 1.234567890123456789 }\n'


class Adapter:
    def __init__(self):
        self.calls = []
        self.closed = False
    def availability(self):
        return {'available':True,'catalog_source_id':'source','reason':None}
    async def catalog(self, disconnect=None):
        self.calls.append('catalog')
        return {'status':'available','catalog_source_id':'source','templates':[]}
    async def preview(self, raw, disconnect=None):
        self.calls.append(raw)
        return {'status':'available','catalog_source_id':'source','plan':PLAN,
                'plan_sha256':hashlib.sha256(PLAN.encode()).hexdigest()}
    async def close(self):
        self.closed = True


def test_auth_no_store_and_exact_raw_plan_transport():
    adapter = Adapter()
    with TestClient(create_app(Settings(TOKEN,(CODE,)), SimpleNamespace(),
                               configurator=adapter)) as client:
        for method, path in [('GET','/api/v1/configurator/catalog'),
                             ('POST','/api/v1/configurator/preview')]:
            assert client.request(method,path,content=RAW).status_code == 401
        assert adapter.calls == []
        result = client.get('/api/v1/configurator/catalog',headers=AUTH)
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        result = client.post('/api/v1/configurator/preview',headers=AUTH,content=RAW)
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        assert result.json()['plan'] == PLAN and adapter.calls[-1] == RAW
        assert client.get('/api/v1/config',headers=AUTH).json()['configurator'] == adapter.availability()
    assert adapter.closed


def test_unconfigured_keeps_old_reads_and_reports_unavailable():
    with TestClient(create_app(Settings(TOKEN,(CODE,)),SimpleNamespace())) as client:
        assert client.get('/healthz').status_code == 200
        config = client.get('/api/v1/config',headers=AUTH).json()
        assert config['configurator'] == {'available':False,'catalog_source_id':None,
                                          'reason':'unconfigured'}
        result = client.get('/api/v1/configurator/catalog',headers=AUTH)
        assert result.status_code == 503 and result.headers['cache-control'] == 'no-store'


def test_http_refusal_has_no_diagnostic_leak_and_no_store():
    from qcl_negf_api.configurator import ConfiguratorError
    class Refusal(Adapter):
        async def preview(self, raw, disconnect=None):
            raise ConfiguratorError('busy')
    with TestClient(create_app(Settings(TOKEN,(CODE,)),SimpleNamespace(),
                               configurator=Refusal())) as client:
        result = client.post('/api/v1/configurator/preview',headers=AUTH,content=RAW)
        assert result.status_code == 409
        assert result.headers['cache-control'] == 'no-store'
        assert result.json() == {'status':'error','error':{'code':'busy','path':'',
            'message':'Configurator request refused'}}
