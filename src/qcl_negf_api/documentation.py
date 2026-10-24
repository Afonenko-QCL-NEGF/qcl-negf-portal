"""Optional, inventory-only source documentation; no release or run-binding verdict."""

import hashlib
import json
import logging
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from qcl_negf_contracts import schema_validator
from qcl_negf_contracts.messages import decode

from .settings import Settings

LOG = logging.getLogger(__name__)
MIB = 1024 * 1024
MEDIA_TYPES = {
    '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
    '.json': 'application/json', '.woff': 'font/woff', '.woff2': 'font/woff2',
    '.ttf': 'font/ttf', '.png': 'image/png', '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
    '.svg': 'image/svg+xml', '.ico': 'image/vnd.microsoft.icon', '.txt': 'text/plain',
}
JOURNEYS = {
    'researcher': 'core/theory/09_scba.html#researcher-scba-route',
    'developer': 'runner/user/configuration.html#developer-configuration-route',
    'operator': 'operator/recovery.html#operator-recovery',
}


class DocumentationJourneys(BaseModel):
    researcher: str | None = None
    developer: str | None = None
    operator: str | None = None


class DocumentationAvailability(BaseModel):
    status: Literal['available', 'unavailable'] = 'unavailable'
    reason: Literal['ok', 'not_configured', 'invalid_configuration', 'bundle_missing',
                    'invalid_bundle'] = 'not_configured'
    source_revision: str | None = None
    bundle_sha256: str | None = None
    url: str | None = None
    journeys: DocumentationJourneys = DocumentationJourneys()


def _relative(value: str) -> bool:
    return bool(value) and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value) and (
        '\\' not in value and '%' not in value and ':' not in value
        and all(part and not part.startswith('.') for part in value.split('/'))
        and str(PurePosixPath(value)) == value
    )


def _public(value: str) -> bool:
    if not _relative(value):
        return False
    path = PurePosixPath(value)
    return (
        (value in {'index.html', 'NOTICE.txt', 'bundle.json'} or value.startswith(
            ('core/', 'runner/', 'operator/', 'assets/', 'help/')))
        and path.suffix.lower() in MEDIA_TYPES
        and not any(part.lower() in {'logs', 'depot', 'depots', 'raw', 'results', 'private'}
                    for part in path.parts)
        and (path.suffix.lower() != '.txt' or value == 'NOTICE.txt'
             or value.startswith('assets/licenses/'))
    )


def _read(root: Path, relative: str, maximum: int) -> bytes:
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError('nonregular payload')
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('nonregular payload')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('payload exceeds limit')
    return raw


def _document(raw: bytes, maximum: int, validator) -> dict:
    # The shared bytes decoder autodetects UTF16/32. Parse the strict UTF8
    # string first, then retain its duplicate-key, finite-number and depth checks.
    json.loads(raw.decode('utf-8', errors='strict'))
    document = decode(raw, maximum=maximum)
    validator.validate(document)
    return document


def _source_refs(value, revisions: dict[str, str]) -> None:
    if isinstance(value, dict):
        if {'component', 'revision', 'path', 'selector', 'sha256'} <= value.keys():
            if (not _relative(value['path']) or value['component'] not in revisions
                    or value['revision'] != revisions[value['component']]):
                raise ValueError('source graph mismatch')
        for child in value.values():
            _source_refs(child, revisions)
    elif isinstance(value, list):
        for child in value:
            _source_refs(child, revisions)


@dataclass(frozen=True)
class Documentation:
    availability: DocumentationAvailability
    payloads: dict[str, tuple[bytes, str, str]]

    @classmethod
    def load(cls, settings: Settings) -> Documentation:
        def unavailable(reason):
            return cls(DocumentationAvailability(reason=reason), {})

        directory, expected = settings.docs_bundle_dir, settings.docs_bundle_sha256
        if directory is None and expected is None:
            return unavailable('not_configured')
        if (not isinstance(directory, str) or not directory or '\x00' in directory
                or not Path(directory).is_absolute() or str(Path(directory)) != directory
                or '..' in Path(directory).parts
                or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
            return unavailable('invalid_configuration')
        root = Path(directory)
        try:
            # Do not follow a symlink in the trusted deployment root either.
            for parent in (root, *root.parents):
                if stat.S_ISLNK(parent.lstat().st_mode):
                    raise ValueError('symlink root')
            if not root.is_dir():
                return unavailable('bundle_missing')
            raw = _read(root, 'bundle.json', 2 * MIB)
        except FileNotFoundError:
            return unavailable('bundle_missing')
        except (OSError, ValueError):
            LOG.warning('Optional documentation unavailable: invalid bundle root')
            return unavailable('invalid_bundle')
        try:
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('manifest digest mismatch')
            validator = schema_validator('model-help.schema.json')
            document = _document(raw, 2 * MIB, validator)
            if document['format'] != 'qcl-negf.docs-bundle.v1':
                raise ValueError('unsupported bundle')
            graph = document['sources']
            revisions = {'root': graph['revision']}
            for component in graph['components']:
                path = component['path']
                if not _relative(path) or path in revisions:
                    raise ValueError('duplicate or invalid component')
                revisions[path] = component['revision']
            schema = document['schema']
            if (schema['component'] != 'components/qcl-negf-contracts'
                    or schema['path'] != 'src/qcl_negf_contracts/schemas/model-help.schema.json'):
                raise ValueError('schema identity mismatch')
            _source_refs(document, revisions)
            files = document['files']
            if len(files) > 10000:
                raise ValueError('inventory exceeds limit')
            payloads = {}
            total = 0
            for entry in files:
                path = entry['path']
                if not _public(path) or path == 'bundle.json' or path in payloads:
                    raise ValueError('invalid inventory path')
                if (entry['bytes'] > 16 * MIB or entry['media_type'] != MEDIA_TYPES[
                        PurePosixPath(path).suffix.lower()]):
                    raise ValueError('invalid inventory metadata')
                total += entry['bytes']
                if total > 256 * MIB:
                    raise ValueError('inventory exceeds limit')
                content = _read(root, path, entry['bytes'])
                if (len(content) != entry['bytes']
                        or hashlib.sha256(content).hexdigest() != entry['sha256']):
                    raise ValueError('payload mismatch')
                payloads[path] = (content, entry['media_type'], entry['sha256'])
            fragment_paths = {item['file'] for item in document['fragments']}
            if {path for path in payloads if path.startswith('help/fragments/')} != fragment_paths:
                raise ValueError('unregistered fragment payload')
            identifiers = set()
            for descriptor in document['fragments']:
                identifier, path = descriptor['id'], descriptor['file']
                if (identifier in identifiers or path != f'help/fragments/{identifier}.json'
                        or path not in payloads):
                    raise ValueError('invalid fragment descriptor')
                identifiers.add(identifier)
                content, _, sha = payloads[path]
                if len(content) > 32768 or descriptor['sha256'] != sha:
                    raise ValueError('invalid fragment bytes')
                fragment = _document(content, 32768, validator)
                if (fragment['format'] != 'qcl-negf.model-help-fragment.v1'
                        or fragment['id'] != identifier
                        or fragment['source_revision'] != graph['revision']):
                    raise ValueError('fragment identity mismatch')
                _source_refs(fragment, revisions)
            payloads['bundle.json'] = (raw, 'application/json', expected)
            revision = graph['revision']
            prefix = f'/docs/{revision}/'
            # An absent journey target cannot become a successful navigation link.
            targets = ['index.html', *JOURNEYS.values(),
                       'runner/api/public.html#reader-api-routes']
            if any(target.split('#')[0] not in payloads for target in targets):
                raise ValueError('journey file missing')
            availability = DocumentationAvailability(
                status='available', reason='ok', source_revision=revision,
                bundle_sha256=expected, url=prefix,
                journeys=DocumentationJourneys(**{key: prefix + path
                                                 for key, path in JOURNEYS.items()}),
            )
            return cls(availability, payloads)
        except Exception as error:
            # Optional docs never disable science/API. Diagnostics contain a fixed
            # category only: validator messages may contain private host paths.
            LOG.warning('Optional documentation unavailable: invalid bundle (%s)',
                        type(error).__name__[:80])
            return unavailable('invalid_bundle')

    def response(self, request: Request, tail: str = '') -> Response:
        if b'%' in request.scope.get('raw_path', b''):
            return JSONResponse({'detail': 'Documentation not found'}, 404)
        parts = tail.split('/', 1)
        reserved = tail == ''
        if not reserved and (len(parts) != 2 or not re.fullmatch('[0-9a-f]{40}', parts[0])):
            return JSONResponse({'detail': 'Documentation not found'}, 404)
        path = '' if reserved else parts[1] or 'index.html'
        if not reserved and not _public(path):
            return JSONResponse({'detail': 'Documentation not found'}, 404)
        if self.availability.status != 'available':
            return JSONResponse({'detail': 'Source documentation unavailable'}, 503,
                                headers={'Cache-Control': 'no-store'})
        if reserved or parts[0] != self.availability.source_revision or path not in self.payloads:
            return JSONResponse({'detail': 'Documentation not found'}, 404)
        content, media, sha = self.payloads[path]
        headers = {'ETag': f'"{sha}"', 'Cache-Control': 'public,max-age=31536000,immutable'}
        conditions = request.headers.get('if-none-match', '').split(',')
        if any(value.strip().removeprefix('W/') in {headers['ETag'], '*'} for value in conditions):
            return Response(status_code=304, headers=headers)
        headers['Content-Type'] = media
        headers['Content-Length'] = str(len(content))
        return Response(b'' if request.method == 'HEAD' else content, headers=headers)
