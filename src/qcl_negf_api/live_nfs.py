"""Explicit U1 performance selection; AiiDA owns origin, Results owns metrics.

The child reads selected RO domains directly. No scientific HDF spool copy or
collector is created here. The retired live-history seam is fail closed pending U2.
"""

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from .saved_nfs import REFS, _NativeDomain, canonical, same, sha, validate_binding
from .saved_results import (
    HASH,
    METADATA,
    OWNER,
    RESPONSE,
    TEMPORARY,
    SavedResultError,
    _bytes,
    _json,
    _path,
)
from .service import ActorUnavailable

PLAN_BYTES = 16 * METADATA
# Immutable maximum public calls: source, probe child, summary child, plan getter.
MODE_PROFILES = (("retained", (1, 0, 1, 1)), ("current", (2, 1, 1, 1)))


def fail(code="identity_mismatch"):
    raise SavedResultError(code)


def performance_query(selection, query):
    if (
        set(query) != {"mode", "anchor_key"}
        or not isinstance(query["mode"], str)
        or query["mode"] not in dict(MODE_PROFILES)
        or not isinstance(query["anchor_key"], str)
        or not HASH.fullmatch(query["anchor_key"])
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", selection["execution_id"])
        or not 1 <= selection["attempt"] <= 9007199254740991
    ):
        fail("selection_required")
    return dict(query)


def checked_response(body):
    """Count before the final full encode; no self-count or cost projection."""
    count = 0
    try:
        for chunk in json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).iterencode(body):
            count += len(chunk.encode("utf-8"))
            if count > RESPONSE:
                fail("budget_exceeded")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SavedResultError("corrupt_result") from exc
    _bytes(body)
    return body


def _root(binding, descriptor, *, payload=False):
    """All descriptors, including unselected ones, match trusted site storage."""
    storage = descriptor["storage"]
    if (
        any(not same(storage.get(k), binding[k]) for k in REFS)
        or storage.get("computer_uuid") not in binding["allowed_computer_uuids"]
        or descriptor["area"] not in ("native_execution", "native_workdir")
        or (payload and descriptor["area"] != "native_execution")
    ):
        fail("unavailable_storage")
    base = _path(storage["relative_execution_root"])
    relative = descriptor["relative_path"]
    return base if relative == "." else base + "/" + _path(relative)


def _source(dto, selection, query, binding):
    from qcl_negf_contracts.telemetry import (
        validate_performance_source,
        validate_performance_source_index,
    )

    try:
        validate_performance_source(dto)
        if not same(dto["provider_selection"], selection):
            fail()
        if dto["status"] == "available":
            if dto["anchor_key"] != query["anchor_key"] or dto["origin"]["mode"] != query["mode"]:
                fail()
            if binding is None:
                fail("unavailable_storage")
            index = validate_performance_source_index(_json(dto["index"]["raw_utf8"]))
            _root(binding, index["technical"]["root"], payload=True)
            for entry in index["entries"]:
                for key in (
                    "runner_anchor_root",
                    "scientific_root",
                    "technical_root",
                    "canonical_plan_root",
                ):
                    _root(binding, entry[key], payload=key != "canonical_plan_root")
        elif "locator" in dto:
            if query["mode"] != "current" or dto["reason"] != "current_snapshot_required":
                fail()
            if binding is None:
                fail("unavailable_storage")
            _root(binding, dto["locator"]["technical_root"], payload=True)
        return dto
    except SavedResultError:
        raise
    except Exception as exc:
        raise SavedResultError("corrupt_result") from exc


def _plan(adapter, dto, selection, check):
    entry = dto["entry"]
    anchor = _json(entry["runner_anchor_utf8"])
    state = anchor["scientific"]["selected_state"]
    source = entry["telemetry"]["source_transport"]
    source_selection = dict(
        run_uuid=selection["run_uuid"],
        **{k: source["selection"][k] for k in ("execution_id", "attempt", "calcjob_uuid")},
    )
    check()
    reply = adapter.service.get_model_help_input(
        source_selection["run_uuid"],
        source_selection["execution_id"],
        state["identity"]["point_id"],
        attempt=source_selection["attempt"],
        calcjob_uuid=source_selection["calcjob_uuid"],
    )
    check()
    try:
        context, frozen = reply["context"], reply["frozen_plan"]
        if (
            set(reply) != {"context", "frozen_plan", "snapshot", "input_status", "release_id"}
            or set(frozen) != {"bytes", "sha256"}
            or any(not same(context.get(k), v) for k, v in source_selection.items())
            or context.get("point_id") != state["identity"]["point_id"]
            or context.get("plan_fingerprint") != source["selection"]["plan_fingerprint"]
        ):
            fail()
        raw = frozen["bytes"]
        if type(raw) is not bytes or not 0 < len(raw) <= PLAN_BYTES:
            fail("budget_exceeded")
        ref = entry["telemetry"]["canonical_plan_ref"]
        pin = dto["origin"]["input_nodes"]["plan"]
        if (
            ref != source["frozen_plan"]
            or ref["path"] != "plan.json"
            or ref["schema"] != "qcl-negf-scientific-plan-v2"
            or ref["media_type"] != "application/json"
            or frozen["sha256"] != sha(raw)
            or any(p["bytes"] != str(len(raw)) or p["sha256"] != sha(raw) for p in (pin, ref))
        ):
            fail()
        return raw, ref
    except SavedResultError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise SavedResultError("identity_mismatch") from exc


def _write_check(check):
    """Forecast only the two owned files, before each parent write/spawn."""
    check()
    forecast = PLAN_BYTES + RESPONSE
    if (
        forecast > OWNER
        or forecast > TEMPORARY
        or shutil.disk_usage("/tmp").free < forecast + OWNER
    ):
        fail("budget_exceeded")
    check()


def perform(adapter, operation, selection, query, cancelled, deadline, check):
    if operation != "performance":
        fail("capability_unavailable")
    query = performance_query(selection, dict(query))  # frozen branch before effects
    selectors = dict(attempt=selection["attempt"], calcjob_uuid=selection["calcjob_uuid"], **query)
    args = (selection["run_uuid"], selection["execution_id"])
    check()
    dto = adapter.service.get_performance_source(*args, **selectors)
    check()
    dto = _source(dto, selection, query, adapter.binding)
    spool = None
    primary = None
    answer = None
    try:
        if dto["status"] == "available" or "locator" in dto:
            check()
            _write_check(check)
            spool = tempfile.TemporaryDirectory(prefix="qcl-performance-", dir="/tmp")
        if "locator" in dto:
            _write_check(check)
            probe = adapter.worker(
                operation,
                dict(
                    stage="source_probe",
                    binding=adapter.binding,
                    binding_sha256=sha(canonical(adapter.binding)),
                    locator=dto["locator"],
                ),
                Path(spool.name),
                cancelled,
                deadline,
            )
            check()
            if set(probe) != {"index_ref", "index_utf8"}:
                fail("corrupt_result")
            check()
            dto = adapter.service.get_performance_source(
                *args, **selectors, current_snapshot={**probe, "anchor_key": query["anchor_key"]}
            )
            check()
            dto = _source(dto, selection, query, adapter.binding)
            if "locator" in dto:
                fail("source_changed")
        if dto["status"] == "available":
            raw, ref = _plan(adapter, dto, selection, check)
            _write_check(check)
            plan = Path(spool.name) / "plan"
            plan.mkdir()
            _write_check(check)
            (plan / "plan.json").write_bytes(raw)
            _write_check(check)
            answer = adapter.worker(
                operation,
                dict(
                    stage="summary",
                    binding=adapter.binding,
                    binding_sha256=sha(canonical(adapter.binding)),
                    source=dto,
                    plan_ref=ref,
                ),
                Path(spool.name),
                cancelled,
                deadline,
            )
            check()
            if set(answer) != {"access", "view"}:
                fail("corrupt_result")
            _checked_view(dto, answer["view"])
            if answer["access"] != _access_claim(adapter.binding):
                fail()
    except BaseException as exc:
        primary = exc
        if (
            getattr(exc, "cleanup_unconfirmed", False)
            or (isinstance(exc, ActorUnavailable) and exc.receipt.outcome == "unknown")
            or (isinstance(exc, SavedResultError) and exc.code == "stopping")
        ):
            exc.cleanup_unconfirmed = True
            adapter.closed = adapter.lease_cleanup_unconfirmed = True
            if spool is not None:
                adapter.pending_spools.append(spool)
                spool = None
        raise
    finally:
        if spool is not None:
            try:
                spool.cleanup()
            except BaseException as exc:
                adapter.closed = adapter.lease_cleanup_unconfirmed = True
                adapter.pending_spools.append(spool)
                if primary is not None:
                    primary.cleanup_unconfirmed = True
                else:
                    error = SavedResultError("unavailable_storage")
                    error.cleanup_unconfirmed = True
                    raise error from exc
    check()
    return checked_response(
        dict(
            schema="qcl-negf.portal-performance.v2",
            operation="performance",
            status=dto["status"],
            provider_selection=selection,
            **query,
            source_metadata=dto,
            access=None if answer is None else answer["access"],
            view=None if answer is None else answer["view"],
            owner_runtime=dict(
                scope="aiida_public_source_getters",
                status="not_measured",
                actual_io_bytes=None,
                heap_bytes=None,
                peak_rss_bytes=None,
                reason="not_reported_by_public_source_getters",
            ),
        )
    )


def _access_claim(binding):
    return dict(
        status="checked_ro_current_request",
        method="local_binding_mountinfo_nofollow_domain",
        config_sha256=sha(canonical(binding)),
        scope="selected_scientific_and_technical_roots",
    )


def _checked_view(source, view):
    state = _json(source["entry"]["runner_anchor_utf8"])["scientific"]["selected_state"]
    if (
        view.get("schema") != "qcl-negf.performance-summary-view.v2"
        or view.get("scope") != "execution_attempt"
        or view.get("status") != "available"
        or not same(view.get("source_state"), state)
        or not same(view.get("transport"), source["entry"]["telemetry"]["expected_transport"])
    ):
        fail("identity_mismatch")


def _read(root, ref, cap):
    """Bounded nofollow regular FileRef read before decoding/copying."""
    name = _path(ref["path"])
    target = root / name
    for p in (*reversed(target.parents), target):
        if p.is_symlink():
            fail("unavailable_storage")
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    context = os.fdopen(fd, "rb")
    primary = None
    try:
        stream = context.__enter__()
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            fail("budget_exceeded")
        raw = stream.read(cap + 1)
        after = os.fstat(stream.fileno())
        named = target.stat(follow_symlinks=False)
        # Reads may update access time; only immutable byte/owner stamps count.
        if any(
            getattr(info, key) != getattr(after, key) or getattr(named, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            fail("source_changed")
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            context.__exit__(
                type(primary), primary, primary.__traceback__
            ) if primary else context.__exit__(None, None, None)
        except BaseException as exc:
            if primary is not None:
                primary.cleanup_unconfirmed = True
                primary.code = "stopping"
            else:
                error = SavedResultError("stopping")
                error.cleanup_unconfirmed = True
                raise error from exc
    if len(raw) > cap:
        fail("budget_exceeded")
    if "bytes" in ref and (ref["bytes"] != str(len(raw)) or ref["sha256"] != sha(raw)):
        fail("source_changed")
    return raw


def read_request(request, spool):
    if request.get("operation") != "performance":
        fail("capability_unavailable")
    stage = request.get("stage")
    expected = {"operation", "stage", "binding", "binding_sha256"} | (
        {"locator"}
        if stage == "source_probe"
        else {"source", "plan_ref"}
        if stage == "summary"
        else set()
    )
    if stage not in ("source_probe", "summary") or set(request) != expected:
        fail("selection_required")
    binding = validate_binding(request["binding"])
    if request["binding_sha256"] != sha(canonical(binding)):
        fail()
    domains = []
    reader = None
    primary = None
    try:

        def domain(descriptor):
            made = _NativeDomain(binding, _root(binding, descriptor, payload=True))
            domains.append(made)
            return made.root

        if stage == "source_probe":
            from qcl_negf_contracts.telemetry import (
                validate_performance_source_index,
                validate_performance_source_pointer,
            )

            locator = request["locator"]
            root = domain(locator["technical_root"])
            if locator["index_pointer"] != dict(
                schema="qcl-negf.performance-source-pointer.v1", path="current-source.json"
            ):
                fail()
            pointer = validate_performance_source_pointer(
                _json(_read(root, locator["index_pointer"], 65536))
            )
            ref = pointer["index"]
            raw = _read(root, ref, METADATA)
            validate_performance_source_index(_json(raw))
            answer = dict(index_ref=ref, index_utf8=raw.decode("utf-8"))
        else:
            from qcl_negf_results.performance_views import (
                PerformanceLimits,
                TelemetrySelection,
                read_performance_summary,
            )
            from qcl_negf_results.state import ResultReadLimits, StateReader

            source = request["source"]
            selection = source["provider_selection"]
            _source(
                source,
                selection,
                dict(mode=source["origin"]["mode"], anchor_key=source["anchor_key"]),
                binding,
            )
            entry = source["entry"]
            state = _json(entry["runner_anchor_utf8"])["scientific"]["selected_state"]
            science = domain(entry["scientific_root"])
            technical = domain(entry["technical_root"])
            if request["plan_ref"] != entry["telemetry"]["canonical_plan_ref"]:
                fail()
            telemetry = TelemetrySelection(
                technical, canonical_plan_root=spool / "plan", **entry["telemetry"]
            )
            limits = ResultReadLimits(
                maximum_metadata_bytes=METADATA,
                maximum_io_bytes=128 * METADATA,
                maximum_catalog_pages=4096,
                maximum_catalog_entries=4096,
                maximum_catalog_bytes=METADATA,
                maximum_open_files=4,
                maximum_record_bytes=METADATA,
                maximum_batch_bytes=METADATA,
                maximum_batch_records=256,
                maximum_hdf_objects=4096,
                maximum_hdf_depth=64,
                maximum_array_bytes=METADATA,
            )
            reader = StateReader(
                science, state_ref=state, expected_identity=state["identity"], limits=limits
            )
            view = read_performance_summary(
                reader, telemetry=telemetry, limits=PerformanceLimits(maximum_objects=16384)
            )
            _checked_view(source, view)
            owner, reader = reader, None
            try:
                owner.close()
            except BaseException as exc:
                exc.cleanup_unconfirmed = True
                raise
            answer = dict(access=_access_claim(binding), view=view)
        for item in domains:
            item.check()
    except BaseException as exc:
        primary = exc
        if getattr(exc, "cleanup_unconfirmed", False):
            exc.code = "stopping"
        raise
    finally:
        errors = []
        if reader is not None:
            try:
                reader.close()
            except BaseException as exc:
                errors.append(exc)
        for item in reversed(domains):
            try:
                item.close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            if primary is not None:
                primary.cleanup_unconfirmed = True
                # Existing child error wire carries only code. Preserve the
                # primary object while stopping this optional consumer.
                primary.code = "stopping"
            else:
                error = SavedResultError("stopping")
                error.cleanup_unconfirmed = True
                raise error from errors[0]
    return answer
