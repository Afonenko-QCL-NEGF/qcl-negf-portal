"""Read-only narrow view: retained context, with no source or scalar projection."""
import re
from uuid import UUID

from qcl_negf_contracts.messages import MAX_PLAN_BYTES
from qcl_negf_contracts.model_help import validate_model_help_view


class MalformedModelHelp(RuntimeError):
    """The actor did not return the coordinated detached DTO."""


def canonical_uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError('noncanonical UUID')
    return value


def selectors(run_uuid, fragment_id, execution_id, point_id, attempt, calcjob_uuid):
    canonical_uuid(run_uuid)
    if not re.fullmatch(r'[a-z][a-z0-9._-]{0,127}', fragment_id):
        raise ValueError('invalid fragment selector')
    for value in (execution_id, point_id):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
            raise ValueError('invalid context selector')
    if attempt is not None and not re.fullmatch(r'[1-9][0-9]*', attempt):
        raise ValueError('invalid attempt selector')
    if calcjob_uuid is not None:
        canonical_uuid(calcjob_uuid)


def model_help_view(dto):
    if (type(dto) is not dict or set(dto) != {
            'context', 'frozen_plan', 'snapshot', 'input_status', 'release_id'}
            or type(dto['context']) is not dict
            or type(dto['frozen_plan']) is not dict
            or set(dto['frozen_plan']) != {'bytes', 'sha256'}
            or type(dto['frozen_plan']['bytes']) is not bytes
            or len(dto['frozen_plan']['bytes']) > MAX_PLAN_BYTES
            or not isinstance(dto['frozen_plan']['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', dto['frozen_plan']['sha256'])
            or dto['snapshot'] is not None
            or dto['input_status'] != {'status': 'unavailable', 'reason': 'inventory_unavailable'}
            or (dto['release_id'] is not None and (
                not isinstance(dto['release_id'], str) or not dto['release_id']))):
        raise MalformedModelHelp('Malformed model help input')
    view = {
        'format': 'qcl-negf.model-help-view.v1', 'context': dict(dto['context']),
        'binding': {'status': 'unknown', 'reason': 'source_binding_missing',
                    'release_id': dto['release_id'], 'release_manifest_sha256': None,
                    'source_revision': None, 'bundle_sha256': None},
        'fragment': None, 'inputs': [],
        'input_status': {'status': 'unavailable', 'reason': 'inventory_unavailable'},
    }
    validate_model_help_view(view, retained={
        'selection': {'context': view['context'], 'release_id': dto['release_id']},
        'metadata': {'frozen_plan': dict(dto['frozen_plan'])},
    })
    return view
