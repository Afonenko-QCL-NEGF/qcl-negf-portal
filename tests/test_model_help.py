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


def test_view_uses_one_public_boundary_with_exact_detached_evidence(dto, monkeypatch):
    from qcl_negf_api import model_help as consumer
    from qcl_negf_contracts.model_help import validate_model_help_view

    before = copy.deepcopy(dto)
    observed = []

    def public_boundary(document, *, retained):
        observed.append((document, retained))
        return validate_model_help_view(document, retained=retained)

    def refuse_local_preflight(*args, **kwargs):
        raise AssertionError('Portal must delegate finite/C1/C2 to the public boundary')

    monkeypatch.setattr(consumer, 'validate_model_help_view', public_boundary, raising=False)
    monkeypatch.setattr(consumer, 'encode', refuse_local_preflight, raising=False)
    monkeypatch.setattr(consumer, 'schema_validator', refuse_local_preflight, raising=False)
    view = model_help_view(dto)
    assert len(observed) == 1
    document, retained = observed[0]
    assert document is view
    assert retained == {
        'selection': {'context': dto['context'], 'release_id': 'recorded-A'},
        'metadata': {'frozen_plan': dto['frozen_plan']},
    }
    assert retained['selection']['context'] is view['context']
    assert retained['metadata']['frozen_plan'] is not dto['frozen_plan']
    assert retained['metadata']['frozen_plan']['bytes'] is dto['frozen_plan']['bytes']
    assert view['binding'] == {
        'status': 'unknown', 'reason': 'source_binding_missing',
        'release_id': 'recorded-A', 'source_revision': None,
        'release_manifest_sha256': None, 'bundle_sha256': None,
    }
    assert view['fragment'] is None and view['inputs'] == []
    assert dto == before


def test_public_boundary_refusal_is_not_bypassed_or_reclassified(dto, monkeypatch):
    from qcl_negf_api import model_help as consumer
    from qcl_negf_contracts.messages import ContractError

    primary = ValueError('literal retained proof refusal')
    refused = ContractError('public validation refused', code='invalid_input')
    refused.__cause__ = primary

    def refuse(document, *, retained):
        raise refused

    monkeypatch.setattr(consumer, 'validate_model_help_view', refuse, raising=False)
    with pytest.raises(ContractError) as caught:
        model_help_view(dto)
    assert caught.value is refused
    assert caught.value.__cause__ is primary


def test_public_boundary_refusal_uses_existing_safe_http_422(dto, monkeypatch):
    from qcl_negf_api import model_help as consumer
    from qcl_negf_contracts.messages import ContractError

    def refuse(document, *, retained):
        raise ContractError('/private/retained/source must not enter HTTP response')

    monkeypatch.setattr(consumer, 'validate_model_help_view', refuse, raising=False)
    with TestClient(create_app(Settings(TOKEN, (CHILD,)), SimpleNamespace(
            get_model_help_input=lambda *args, **kwargs: dto))) as client:
        reply = client.get(f'/api/v1/runs/{RUN}/model-help', params=QUERY, headers=AUTH)
    assert reply.status_code == 422
    assert reply.json() == {'detail': 'Model help validation was refused'}
    assert reply.headers['cache-control'] == 'no-store'
    assert 'private' not in reply.text


def malformed_consumer_dto(dto, change):
    """Manufactured bad actor data; no Contracts validator is an oracle."""
    if change == 'boolean_attempt':
        dto['context']['attempt'] = True
    elif change == 'missing_fingerprint':
        del dto['context']['plan_fingerprint']
    elif change == 'invalid_unicode':
        dto['context']['model_revision'] = '\udfff'
    elif change == 'encoded_budget':
        dto['release_id'] = 'x' * 65536
    elif change == 'integer_limit':
        dto['context']['attempt'] = 10 ** 5000
    elif change == 'digest':
        dto['frozen_plan']['sha256'] = '0' * 64
    elif change in {'invalid_json', 'retained_integer_limit'}:
        raw = (b'{"private":"/private/unpublished",' if change == 'invalid_json' else
               b'{"integer":' + b'1' * 5001 + b'}')
        dto['frozen_plan'] = {'bytes': raw, 'sha256': hashlib.sha256(raw).hexdigest()}
    elif change == 'context_not_mapping':
        dto['context'] = None
    elif change == 'frozen_plan_not_mapping':
        dto['frozen_plan'] = None
    elif change == 'frozen_plan_text_not_bytes':
        dto['frozen_plan']['bytes'] = '/private/unpublished'
    elif change == 'frozen_plan_extra_field':
        dto['frozen_plan']['path'] = '/private/unpublished'
    else:
        raise AssertionError('unknown manufactured case')


@pytest.mark.parametrize('change', [
    'boolean_attempt', 'missing_fingerprint', 'invalid_unicode',
    'encoded_budget', 'integer_limit', 'retained_integer_limit',
])
def test_real_document_errors_use_public_contract_family_and_primary_cause(dto, change):
    from jsonschema import ValidationError
    from qcl_negf_contracts.messages import ContractError

    malformed_consumer_dto(dto, change)
    before = copy.deepcopy(dto)
    with pytest.raises(ContractError) as caught:
        model_help_view(dto)
    assert caught.value.code == 'invalid_input'
    if change in {'boolean_attempt', 'missing_fingerprint'}:
        assert isinstance(caught.value.__cause__, ValidationError)
    elif change == 'invalid_unicode':
        assert isinstance(caught.value.__cause__, UnicodeError)
    elif change in {'integer_limit', 'retained_integer_limit'}:
        assert type(caught.value.__cause__) is ValueError
    assert dto == before


@pytest.mark.parametrize('change,status', [
    ('boolean_attempt', 422), ('missing_fingerprint', 422), ('invalid_unicode', 422),
    ('digest', 422), ('invalid_json', 422), ('retained_integer_limit', 422),
    ('context_not_mapping', 503), ('frozen_plan_not_mapping', 503),
    ('frozen_plan_text_not_bytes', 503), ('frozen_plan_extra_field', 503),
])
def test_actual_malformed_actor_data_keeps_safe_http_status(dto, change, status):
    malformed_consumer_dto(dto, change)
    before = copy.deepcopy(dto)
    with TestClient(create_app(Settings(TOKEN, (CHILD,)), SimpleNamespace(
            get_model_help_input=lambda *args, **kwargs: dto))) as client:
        reply = client.get(f'/api/v1/runs/{RUN}/model-help', params=QUERY, headers=AUTH)
    assert reply.status_code == status
    assert reply.json() == {'detail': (
        'Model help validation was refused' if status == 422 else
        'Model help service is unavailable')}
    assert reply.headers['cache-control'] == 'no-store'
    assert 'private' not in reply.text
    assert dto == before
