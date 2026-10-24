"""Synthetic serving fixtures; these are not rendered DOC04 acceptance evidence."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings

REV = 'a' * 40
TOKEN = '0123456789abcdef' * 4
CODE = '10dd2a4c-031b-425c-90b8-2f7ea564d313'
AUTH = {'Authorization': f'Bearer {TOKEN}'}
TARGETS = {
    'researcher': 'core/theory/09_scba.html#researcher-scba-route',
    'developer': 'runner/user/configuration.html#developer-configuration-route',
    'operator': 'operator/recovery.html#operator-recovery',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reference(component='components/qcl-negf-contracts',
              path='src/qcl_negf_contracts/schemas/model-help.schema.json'):
    return dict(component=component, revision='b' * 40, path=path,
                selector={'kind': 'file', 'value': ''}, sha256='c' * 64)


@pytest.fixture
def bundle(tmp_path):
    payloads = {'index.html': b'<!doctype html><title>Synthetic docs</title>'}
    for target in TARGETS.values():
        path, anchor = target.split('#')
        payloads[path] = f'<!doctype html><h1 id="{anchor}">Synthetic</h1>'.encode()
    payloads['runner/api/public.html'] = b'<h1 id="reader-api-routes">Synthetic</h1>'
    for path, content in {
        'assets/runtime/qcl-fixed-light.css': b':root{color-scheme:light}',
        'assets/search.js': b'const syntheticSearch = true;',
        'assets/search.json': b'{}', 'assets/font.woff2': b'synthetic-font',
    }.items():
        payloads[path] = content
    fragment = dict(format='qcl-negf.model-help-fragment.v1', id='synthetic',
                    source_revision=REV, title='Synthetic', summary='', scope='baseline',
                    meaning_source=reference(),
                    full={'section': 'core', 'path': 'theory/09_scba.html',
                          'anchor': 'researcher-scba-route'},
                    related=[], equations=[], inputs=[], api=[], assumptions=[], limitations=[])
    payloads['help/fragments/synthetic.json'] = json.dumps(fragment).encode()
    mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
            '.json': 'application/json', '.woff2': 'font/woff2'}
    manifest = dict(
        format='qcl-negf.docs-bundle.v1',
        sources={'format': 'qcl-negf.git-graph.v1', 'revision': REV,
                 'components': [{'path': 'components/qcl-negf-contracts',
                                 'revision': 'b' * 40,
                                 'url': 'https://example.invalid/contracts'}]},
        schema=reference(), generator=dict(
            name='Documenter', version='1.17.0', uuid='e30172f5-a6a5-5a46-863b-614d45cd2de4',
            git_tree_sha1='56e9c37b5e7c3b4f080ab1da18d72d5c290e184a', julia_version='1.13.0',
            project_sha256='c' * 64, manifest_sha256='c' * 64, recipes=[],
            toolchain_lock=reference()),
        fragments=[{'id': 'synthetic', 'file': 'help/fragments/synthetic.json',
                    'sha256': digest(payloads['help/fragments/synthetic.json'])}],
        files=[{'path': path, 'media_type': mime['.' + path.rsplit('.', 1)[1]],
                'bytes': len(raw), 'sha256': digest(raw)} for path, raw in payloads.items()],
        aliases=[])
    for path, raw in payloads.items():
        output = tmp_path / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
    def write(document=manifest, raw=None):
        raw = json.dumps(document).encode() if raw is None else raw
        (tmp_path / 'bundle.json').write_bytes(raw)
        return Settings(TOKEN, (CODE,), docs_bundle_dir=str(tmp_path),
                        docs_bundle_sha256=digest(raw))
    return tmp_path, manifest, payloads, write


def client(settings):
    return TestClient(create_app(settings, SimpleNamespace()))


def test_unconfigured_prefix_is_reserved_and_api_survives():
    with client(Settings(TOKEN, (CODE,))) as http:
        for path in ['/docs', '/docs/', f'/docs/{REV}/index.html']:
            response = http.get(path)
            assert response.status_code == 503
            assert response.headers['cache-control'] == 'no-store'
        assert http.get('/healthz').status_code == 200
        assert http.get('/api/v1/config').status_code == 401
        config = http.get('/api/v1/config', headers=AUTH)
        assert config.status_code == 200
        assert config.json()['documentation'] == {
            'status': 'unavailable', 'reason': 'not_configured', 'source_revision': None,
            'bundle_sha256': None, 'url': None,
            'journeys': dict.fromkeys(TARGETS),
        }


def test_exact_inventory_bytes_headers_and_journeys(bundle):
    root, manifest, payloads, write = bundle
    settings = write()
    with client(settings) as http:
        config = http.get('/api/v1/config', headers=AUTH).json()['documentation']
        assert config['status'] == 'available'
        assert config['source_revision'] == REV
        assert config['bundle_sha256'] == settings.docs_bundle_sha256
        assert config['url'] == f'/docs/{REV}/'
        assert config['journeys'] == {key: f'/docs/{REV}/{value}' for key, value in TARGETS.items()}
        for entry in manifest['files']:
            response = http.get(f'/docs/{REV}/{entry["path"]}')
            assert response.status_code == 200
            assert response.content == payloads[entry['path']]
            assert response.headers['content-type'].split(';')[0] == entry['media_type']
            assert response.headers['etag'] == f'"{entry["sha256"]}"'
            assert response.headers['cache-control'] == 'public,max-age=31536000,immutable'
            assert "unsafe-inline" not in response.headers['content-security-policy']
            head = http.head(f'/docs/{REV}/{entry["path"]}')
            assert head.status_code == 200 and head.content == b''
            assert head.headers['content-length'] == str(entry['bytes'])
            assert http.get(f'/docs/{REV}/{entry["path"]}', headers={
                'If-None-Match': response.headers['etag']}).status_code == 304
        response = http.get(f'/docs/{REV}/bundle.json')
        assert response.content == (root / 'bundle.json').read_bytes()
        assert response.headers['etag'] == f'"{settings.docs_bundle_sha256}"'
        assert http.get(f'/docs/{REV}/').content == payloads['index.html']
        for path in ['private.txt', 'assets/unlisted.js', '../secret', '%252e%252e/secret',
                     'assets/%5csecret', 'assets/.secret', 'bundle.json/extra']:
            assert http.get(f'/docs/{REV}/{path}').status_code == 404
        assert http.get(f'/docs/{"d" * 40}/index.html').status_code == 404
        assert http.get('/docs/current/index.html').status_code == 404
        assert http.get(f'/docs?tail={REV}/index.html').status_code == 404
        assert http.get('/docs%2f').status_code == 404


@pytest.mark.parametrize('case', ['half', 'hash', 'relative', 'missing', 'duplicate', 'nan',
                                  'wrong_format', 'duplicate_path', 'symlink', 'private',
                                  'tamper', 'fragment', 'schema_identity', 'utf16', 'utf32',
                                  'fragment_shape', 'index_missing', 'unregistered_fragment'])
def test_invalid_optional_bundle_never_disables_api(bundle, case):
    root, document, payloads, write = bundle
    expected = 'invalid_bundle'
    if case in {'utf16', 'utf32'}:
        settings = write(raw=json.dumps(document).encode('utf-16' if case == 'utf16' else 'utf-32'))
    elif case == 'duplicate':
        settings = write(raw=b'{"format":1,"format":2}')
    elif case == 'nan':
        settings = write(raw=b'{"value":NaN}')
    else:
        if case == 'index_missing':
            document['files'] = [item for item in document['files']
                                 if item['path'] != 'index.html']
        if case == 'unregistered_fragment':
            path = 'help/fragments/unregistered.json'
            raw = b'{not valid JSON}'
            (root / path).write_bytes(raw)
            document['files'].append({'path': path, 'media_type': 'application/json',
                                      'bytes': len(raw), 'sha256': digest(raw)})
        if case == 'fragment_shape':
            path = 'help/fragments/synthetic.json'
            raw = b'{"format":"qcl-negf.model-help-fragment.v1"}'
            (root / path).write_bytes(raw)
            document['fragments'][0]['sha256'] = digest(raw)
            entry = next(item for item in document['files'] if item['path'] == path)
            entry.update(bytes=len(raw), sha256=digest(raw))
        if case == 'wrong_format':
            document['format'] = 'unsupported'
        if case == 'duplicate_path':
            document['files'].append(document['files'][0])
        if case == 'private':
            document['files'][0]['path'] = 'private/secret.txt'
        if case == 'fragment':
            document['fragments'][0]['sha256'] = 'd' * 64
        if case == 'schema_identity':
            document['schema']['revision'] = 'd' * 40
        if case == 'tamper':
            (root / 'index.html').write_bytes(b'tampered')
        if case == 'symlink':
            (root / 'index.html').unlink()
            (root / 'index.html').symlink_to(root / 'runner/api/public.html')
        settings = write()
        if case == 'half':
            settings = replace(settings, docs_bundle_sha256=None)
            expected = 'invalid_configuration'
        if case == 'hash':
            settings = replace(settings, docs_bundle_sha256='bad')
            expected = 'invalid_configuration'
        if case == 'relative':
            settings = replace(settings, docs_bundle_dir='relative')
            expected = 'invalid_configuration'
        if case == 'missing':
            (root / 'bundle.json').unlink()
            expected = 'bundle_missing'
    with client(settings) as http:
        assert http.get('/healthz').status_code == 200
        response = http.get('/api/v1/config', headers=AUTH)
        assert response.status_code == 200
        projection = response.json()['documentation']
        assert projection['reason'] == expected
        assert projection['url'] is None
        response = http.get(f'/docs/{REV}/index.html')
        assert response.status_code == 503
        assert str(root) not in response.text


def test_settings_read_optional_pair_without_rejecting_bad_docs(monkeypatch, tmp_path):
    token = tmp_path / 'token'
    token.write_text(TOKEN)
    monkeypatch.setenv('QCL_NEGF_API_TOKEN_FILE', str(token))
    monkeypatch.setenv('QCL_NEGF_ALLOWED_CODES', CODE)
    monkeypatch.setenv('QCL_NEGF_DOCS_BUNDLE_DIR', 'relative')
    monkeypatch.setenv('QCL_NEGF_DOCS_BUNDLE_SHA256', 'bad')
    settings = Settings.from_environment()
    assert settings.docs_bundle_dir == 'relative'
    assert settings.docs_bundle_sha256 == 'bad'


def test_docs_misses_never_reach_root_static_fallback(bundle, monkeypatch):
    import qcl_negf_api.api as api_module

    root, _, _, write = bundle
    static = root / 'web' / 'static'
    static.mkdir(parents=True)
    (static / 'index.html').write_text('<h1>Portal root</h1>')
    monkeypatch.setattr(api_module, '__file__', str(root / 'web' / 'api.py'))
    with client(write()) as http:
        assert 'Portal root' in http.get('/').text
        for path in [f'/docs/{REV}/missing.html', '/docs/current/index.html', '/docs']:
            response = http.get(path)
            assert response.status_code == 404
            assert 'Portal root' not in response.text
    with client(Settings(TOKEN, (CODE,))) as http:
        assert http.get('/').status_code == 200
        assert http.get(f'/docs/{REV}/index.html').status_code == 503
        assert http.get('/docs%2f').status_code == 404
        assert http.get(f'/docs?tail={REV}/index.html').status_code == 503
        for path in ['/docs/current/index.html', f'/docs/{REV}/%252e%252e/secret']:
            assert http.get(path).status_code == 404
