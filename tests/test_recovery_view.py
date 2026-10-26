"""Read-only recovery gateway and HTML entry policy; synthetic metadata only."""
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from qcl_negf_api import api as module
from qcl_negf_api.service import OPERATIONS, MUTATIONS, ActorDeadline, ActorReceipt
from qcl_negf_api.settings import Settings

TOKEN = '0123456789abcdef' * 4
UUID = '563cc50c-0098-4d67-8f35-ad041b186721'
AUTH = {'Authorization': f'Bearer {TOKEN}'}

@pytest.fixture
def client(tmp_path, monkeypatch):
    static = tmp_path / 'static'
    (static / 'assets').mkdir(parents=True)
    (static / 'index.html').write_text('<html>old /assets/hash.js</html>')
    (static / 'assets/hash.js').write_text('const light = true;')
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'api.py'))
    calls = []
    def listing(**kwargs):
        calls.append(('list', kwargs))
        return [{'uuid': UUID, 'kind': 'recovery_execution', 'caller': None}]
    def detail(identifier):
        calls.append(('detail', identifier))
        if identifier != UUID:
            raise LookupError(identifier)
        return {'uuid': UUID, 'kind': 'recovery_execution', 'caller': None, 'children': []}
    service = SimpleNamespace(list_recovery_executions=listing, get_recovery_execution=detail,
                              list_runs=lambda **kw: [{'uuid': 'plan-only'}])
    with TestClient(module.create_app(Settings(TOKEN, (UUID,)), service)) as value:
        yield value, service, calls, static


def test_recovery_bearer_admission_and_existing_runs(client):
    http, service, calls, _ = client
    for path in ('/api/v1/recovery-executions', f'/api/v1/recovery-executions/{UUID}'):
        assert http.get(path).status_code == 401
        assert http.get(path, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert calls == []
    response = http.get('/api/v1/recovery-executions?limit=25&offset=50', headers=AUTH)
    assert response.status_code == 200
    assert response.json()['recovery_executions'][0]['caller'] is None
    assert calls == [('list', {'limit': 25, 'offset': 50})]
    assert response.headers['cache-control'] == 'no-store'
    assert http.get(f'/api/v1/recovery-executions/{UUID}', headers=AUTH).json()['uuid'] == UUID
    assert http.get('/api/v1/runs', headers=AUTH).json() == {'runs': [{'uuid': 'plan-only'}]}
    for suffix in ('?limit=0', '?limit=101', '?offset=-1'):
        assert http.get('/api/v1/recovery-executions' + suffix, headers=AUTH).status_code == 422
    assert http.get('/api/v1/recovery-executions/invalid', headers=AUTH).status_code == 422
    assert http.get('/api/v1/recovery-executions/00000000-0000-0000-0000-000000000000', headers=AUTH).status_code == 404
    assert http.post(f'/api/v1/recovery-executions/{UUID}/kill', headers=AUTH).status_code in (404, 405)
    assert {'list_recovery_executions', 'get_recovery_execution'} <= OPERATIONS
    assert not {'list_recovery_executions', 'get_recovery_execution'} & MUTATIONS

@pytest.mark.parametrize('error,status', [(ValueError('invalid metadata'),422),
    (RuntimeError('offline'),503),
    (ActorDeadline('operation_deadline', ActorReceipt('op', 'get_recovery_execution', 'not_started', 'blocked')),504)])
def test_recovery_errors_preserve_http_boundary(client, error, status):
    http, service, _, _ = client
    def fail(identifier):
        raise error
    service.get_recovery_execution = fail
    assert http.get(f'/api/v1/recovery-executions/{UUID}', headers=AUTH).status_code == status

@pytest.mark.parametrize('path', ['/', '/index.html'])
def test_current_html_ignores_colliding_old_validators(client, path):
    http, _, _, static = client
    old = http.get(path)
    assert old.status_code == 200
    index = static / 'index.html'
    stat = index.stat()
    current = b'<html>new /assets/hash.js</html>'
    assert len(current) == stat.st_size
    index.write_bytes(current)
    os.utime(index, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    for condition in ({'If-None-Match': old.headers['etag']},
                      {'If-Modified-Since': old.headers['last-modified']}):
        for method in ('GET', 'HEAD'):
            response = http.request(method, path, headers=condition)
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-cache, must-revalidate'
            assert response.headers['content-type'].startswith('text/html')
            assert response.content == (current if method == 'GET' else b'')
            assert "frame-ancestors 'none'" in response.headers['content-security-policy']
            assert response.headers['x-content-type-options'] == 'nosniff'
    asset = http.get('/assets/hash.js')
    assert asset.content == b'const light = true;'
    assert http.get('/assets/hash.js', headers={'If-None-Match': asset.headers['etag']}).status_code == 304
    for other in ('/assets/hash.js', '/docs', '/missing', '/api/v1/config'):
        response = http.get(other, headers=AUTH)
        assert response.headers.get('cache-control') != 'no-cache, must-revalidate'
    assert http.get('/api/v1/config', headers=AUTH).headers['cache-control'] == 'no-store'
