"""Active closed-prefix access; no L1 quiescence, registration or whole-tree read.

Only detached child opens the trusted RO alias. Results owns native validation,
same-open leaf copies, history decoding and performance semantics.
"""
from pathlib import Path
import re
import tempfile

from .saved_results import HASH, METADATA, SavedResultError, _history_selection, _path
from .saved_nfs import _access, same, sha, validate_binding


def fail(code="identity_mismatch"):
    raise SavedResultError(code)


def perform(adapter, operation, selection, query, cancelled, deadline, check):
    query = dict(query)
    selectors = {k: selection[k] for k in ("attempt", "calcjob_uuid")}
    args = (selection["run_uuid"], selection["execution_id"])
    if operation == "performance":
        if query:
            fail("selection_required")
        dto = adapter.service.get_performance_source(*args, **selectors)
        check()
        if dto.get("status") == "unavailable":
            reason = dto.get("reason")
            if not isinstance(reason, str) or len(reason) > 256:
                fail("corrupt_result")
            return dict(status="unavailable", reason=reason, provider_selection=selection,
                        scope="execution_attempt")
        binding = dto.get("binding")
        if not isinstance(binding, dict) or any(
            not same(binding.get("selection", {}).get(k), selection[k])
            for k in ("calcjob_uuid", "execution_id", "attempt")
        ):
            fail()
        raw = dto.get("sidecar_utf8")
        if not isinstance(raw, str) or len(raw.encode()) > METADATA:
            fail("budget_exceeded")
        request = dict(sidecar_utf8=raw, expected_binding=binding)
    else:
        allowed = {"point_id", "source_attempt"}
        if operation == "live-history":
            allowed |= {"phase", "selection", "index_sha256", "generation"}
            _history_selection(query.get("phase"), query.get("selection"))
            if not isinstance(query.get("index_sha256"), str) or not HASH.fullmatch(query["index_sha256"]):
                fail("selection_required")
            if type(query.get("generation")) is not int or query["generation"] < 1:
                fail("selection_required")
        if (set(query) != allowed or type(query.get("source_attempt")) is not int
                or query["source_attempt"] < 1 or not isinstance(query.get("point_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", query["point_id"])):
            fail("selection_required")
        if adapter.binding is None:
            fail("unavailable_storage")
        binding = adapter.binding
        dto = adapter.service.get_live_history_source(
            *args, **selectors, point_id=query["point_id"], source_attempt=query["source_attempt"],
            namespace_id=binding["namespace_id"],
        )
        check()
        expected = dto.get("selection")
        if not isinstance(expected, dict) or any(not same(expected.get(k), v) for k, v in
                {**selection, "point_id": query["point_id"], "source_attempt": query["source_attempt"]}.items()):
            fail()
        storage = dto.get("storage", {})
        if (dto.get("schema") != "qcl-negf.live-source.v1" or dto.get("status") != "available"
                or dto.get("access_mode") != "selected_closed_copy"
                or dto.get("descriptor_verified") is not False
                or any(not same(storage.get(k), binding[k]) for k in ("namespace_id", "storage_identity"))
                or storage.get("computer_uuid") not in binding["allowed_computer_uuids"]):
            fail("unavailable_storage")
        relative = _path(storage.get("relative_job_root"))
        if dto.get("descriptor_path") != relative + "/live/current.json":
            fail()
        request = dict(binding=binding, relative_job_root=relative, expected_selection=expected,
                       query=query)
    spool = tempfile.TemporaryDirectory(prefix="qcl-live-", dir="/tmp")
    try:
        check()
        answer = adapter.worker(operation, request, Path(spool.name), cancelled, deadline)
        check()
    except BaseException as error:
        if getattr(error, "cleanup_unconfirmed", False):
            adapter.closed = adapter.lease_cleanup_unconfirmed = True
            adapter.pending_spools.append(spool)
            spool = None
        raise
    finally:
        if spool is not None:
            try:
                spool.cleanup()
            except OSError:
                adapter.closed = True
                fail("unavailable_storage")
    return {**answer, "provider_selection": selection}


def read_request(request, spool):
    operation = request["operation"]
    if operation == "performance":
        from qcl_negf_results.performance_views import read_performance_summary
        if set(request) != {"operation", "sidecar_utf8", "expected_binding"}:
            fail("selection_required")
        raw = request["sidecar_utf8"].encode()
        if len(raw) > METADATA:
            fail("budget_exceeded")
        target = spool / "performance-binding.json"
        target.write_bytes(raw)
        return read_performance_summary(target, expected_binding=request["expected_binding"])
    from qcl_negf_results._capture_io import small_json
    from qcl_negf_results.live_views import LiveHistoryLimits, describe_live_history, read_live_history
    if set(request) != {"operation", "binding", "relative_job_root", "expected_selection", "query"}:
        fail("selection_required")
    binding = validate_binding(request["binding"])
    root = _access(binding["root"], _path(request["relative_job_root"]), ["live/current.json"])
    descriptor, raw = small_json(root / "live/current.json", 16384)
    owner = request["expected_selection"]
    native = {"point_id": owner["point_id"], "execution_id": owner["execution_id"],
              "attempt": owner["source_attempt"], "plan_fingerprint": owner["plan_fingerprint"]}
    identity = descriptor.get("identity")
    if (descriptor.get("schema") != "qcl-negf.live-prefix.v1"
            or descriptor.get("access_mode") != "selected_closed_copy"
            or not isinstance(identity, dict)
            or any(not same(identity.get(k), v) for k, v in native.items())
            or any(k in identity and identity[k] != owner[k] for k in ("run_uuid", "calcjob_uuid"))):
        fail()
    index = _path(descriptor.get("index_path"))
    if not index.endswith("/history/index.json"):
        fail()
    _access(binding["root"], request["relative_job_root"], [index])
    # Inspect bounded references before payload so every selected leaf also
    # passes the existing RO NFS submount policy. Results rechecks native bytes.
    index_value, index_raw = small_json(root / index, METADATA - len(raw))
    if sha(index_raw) != descriptor.get("index_sha256"):
        fail("source_changed")
    segments = index_value.get("segments")
    if not isinstance(segments, list) or len(segments) > 4096:
        fail("budget_exceeded")
    parent = str(Path(index).parent)
    paths = [parent + "/" + _path(row.get("path")) for row in segments if isinstance(row, dict)]
    if len(paths) != len(segments):
        fail("corrupt_result")
    _access(binding["root"], request["relative_job_root"], paths)
    if len(raw) + len(index_raw) >= METADATA:
        fail("budget_exceeded")
    limits = LiveHistoryLimits(maximum_io_bytes=128 * 1024 * 1024 - len(raw) - len(index_raw),
                               maximum_metadata_bytes=METADATA - len(raw) - len(index_raw))
    query = request["query"]
    if operation == "live-history":
        if (not same(descriptor.get("index_sha256"), query["index_sha256"])
                or not same(descriptor.get("generation"), query["generation"])):
            fail("source_changed")
        local = spool / "selected"
        local.mkdir()
        answer = read_live_history(root / index, query["phase"], query["selection"],
            expected_selection=owner, expected_index_sha256=query["index_sha256"],
            expected_generation=query["generation"], spool=local, limits=limits)
    else:
        answer = describe_live_history(root / index, expected_selection=owner, limits=limits)
    if (not same(answer["index_sha256"], descriptor.get("index_sha256"))
            or not same(answer["generation"], descriptor.get("generation"))
            or any(not same(answer["cutoff"][k], descriptor.get(k)) for k in ("scba_rows", "outer_rows"))):
        fail("source_changed")
    return {**answer, "descriptor_verified": True,
            "descriptor_sha256": sha(raw),
            "live_publication_incomplete": descriptor.get("live_publication_incomplete"),
            "publication_route": descriptor.get("route"),
            "descriptor_read_bytes": len(raw)}
