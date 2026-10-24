"""Narrow unknown view over actual ordinary frozen-plan bytes (no producer proof)."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.model_help import model_help_view
from qcl_negf_api.settings import Settings

RUN = 'e7f5313d-22fd-49fa-9be5-85fbe29e1974'
CHILD = '10dd2a4c-031b-425c-90b8-2f7ea564d313'
TOKEN = '0123456789abcdef' * 4
AUTH = {'Authorization': f'Bearer {TOKEN}'}
QUERY = {'fragment_id': 'unpublished-id', 'execution_id': 'execution-1',
         'point_id': 'point-1', 'attempt': '2', 'calcjob_uuid': CHILD}

@pytest.fixture
def dto():
    raw = (Path(__file__).parent / 'fixtures/model-help-plan.json').read_bytes()
    plan = json.loads(raw)
    assert 'configuration' not in plan['points'][0]
    return {'context': {'run_uuid': RUN, 'calcjob_uuid': CHILD, 'attempt': 2,
        'execution_id': 'execution-1', 'point_id': 'point-1',
        'plan_fingerprint': plan['fingerprint'],
        'scientific_fingerprint': plan['scientific_fingerprint'],
        'model_revision': plan['model_revision'],
        'configuration_hash': plan['points'][0]['effective_inputs']['configuration_hash']},
        'frozen_plan': {'bytes': raw, 'sha256': hashlib.sha256(raw).hexdigest()},
        'snapshot': None, 'release_id': 'recorded-A',
        'input_status': {'status': 'unavailable', 'reason': 'inventory_unavailable'}}


def test_ordinary_plan_unknown_is_not_current_docs(dto, monkeypatch):
    monkeypatch.setenv('QCL_NEGF_RELEASE_ID', 'current-B')
    before = copy.deepcopy(dto)
    view = model_help_view(dto)
    assert dto == before
    assert view['context'] == dto['context']
    assert view['binding'] == {'status': 'unknown', 'reason': 'source_binding_missing',
        'release_id': 'recorded-A', 'source_revision': None,
        'release_manifest_sha256': None, 'bundle_sha256': None}
    assert view['fragment'] is None and view['inputs'] == []
    assert view['input_status'] == dto['input_status']


@pytest.mark.parametrize('change', ['digest', 'fingerprint', 'point'])
def test_supplied_contradiction_fails_closed(dto, change):
    if change == 'digest':
        dto['frozen_plan']['sha256'] = '0' * 64
    if change == 'fingerprint':
        dto['context']['plan_fingerprint'] = '0' * 64
    if change == 'point':
        dto['context']['point_id'] = 'not-a-member'
    with pytest.raises(ValueError):
        model_help_view(dto)


def test_route_auth_selectors_exact_dispatch_and_safe_errors(dto):
    calls = []
    result = dto
    def read(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result
    with TestClient(create_app(Settings(TOKEN, (CHILD,)),
            SimpleNamespace(get_model_help_input=read))) as client:
        path = f'/api/v1/runs/{RUN}/model-help'
        assert client.get(path, params=QUERY).status_code == 401
        assert calls == []
        for bad in ({'point_id': '../x'}, {'attempt': '0'}, {'attempt': '1.0'},
                    {'calcjob_uuid': CHILD.upper()}, {'fragment_id': 'x\n'}, {'proof': 'x'}):
            assert client.get(path, params={**QUERY, **bad}, headers=AUTH).status_code == 422
        assert calls == []
        reply = client.get(path, params=QUERY, headers=AUTH)
        assert reply.status_code == 200
        assert reply.headers['cache-control'] == 'no-store'
        assert calls == [((RUN, 'execution-1', 'point-1'), {'attempt': 2, 'calcjob_uuid': CHILD})]
        result = {'error': 'selection_conflict'}
        assert client.get(path, params=QUERY, headers=AUTH).status_code == 409
        result = {'broken': True}
        assert client.get(path, params=QUERY, headers=AUTH).status_code == 503
        result = ValueError('/private/secret/path')
        reply = client.get(path, params=QUERY, headers=AUTH)
        assert reply.status_code == 422 and 'private' not in reply.text
        result = LookupError('missing')
        assert client.get(path, params=QUERY, headers=AUTH).status_code == 404


def test_actor_operation_is_read_only():
    from qcl_negf_api.service import MUTATIONS, OPERATIONS
    assert 'get_model_help_input' in OPERATIONS
    assert 'get_model_help_input' not in MUTATIONS


def test_context_never_substitutes_browser_selection(dto):
    with TestClient(create_app(Settings(TOKEN, (CHILD,)), SimpleNamespace(
            get_model_help_input=lambda *args, **kwargs: dto))) as client:
        for key, value in [('attempt', '1'), ('point_id', 'point-2'),
                           ('calcjob_uuid', '20dd2a4c-031b-425c-90b8-2f7ea564d313')]:
            reply = client.get(f'/api/v1/runs/{RUN}/model-help',
                               params={**QUERY, key: value}, headers=AUTH)
            assert reply.status_code == 422
