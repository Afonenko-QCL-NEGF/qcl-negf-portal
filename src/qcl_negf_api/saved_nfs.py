"""Registered-source transport only; native semantics belong to Results/AiiDA.

No root access occurs outside child_access. Binding startup reads local config only.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from .saved_results import METADATA, OWNER, SavedResultError, _bytes, _json, _path

REFS = (
    "namespace_id",
    "storage_identity",
    "retention_policy_id",
    "retention_owner_id",
    "hold_ref",
    "access_binding_ref",
)
BINDING_KEYS = {*REFS, "root", "allowed_computer_uuids"}
ROOT = "/run/qcl-negf-saved"


def fail(code="corrupt_result"):
    raise SavedResultError(code)


def canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except ValueError, TypeError, UnicodeError, RecursionError:
        fail()


def same(a, b):
    return type(a) is type(b) and canonical(a) == canonical(b)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            fail("selection_required")
    except ValueError, TypeError, AttributeError:
        fail("selection_required")
    return value


def stored_uuid(value):
    try:
        return uuid(value)
    except SavedResultError:
        fail()


def digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        fail()
    return value


def path(value, *, reserved=False):
    value = _path(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        fail()
    if not reserved and value.split("/")[0] == "_aiida":
        fail("unsupported_contract")
    return value


def provider(query):
    query = dict(query)
    kind = query.pop("provider", "aiida_retrieved")
    source = {"provider": kind, "source_uuid": None, "source_sha256": None}
    if kind == "nfs_registered":
        try:
            source["source_uuid"] = uuid(query.pop("source_uuid"))
            source["source_sha256"] = digest(query.pop("source_sha256"))
        except KeyError, SavedResultError:
            fail("selection_required")
    elif kind != "aiida_retrieved" or "source_uuid" in query or "source_sha256" in query:
        fail("selection_required")
    return source, query


def validate_binding(value):
    if not isinstance(value, dict) or set(value) != BINDING_KEYS or value["root"] != ROOT:
        fail("unavailable_storage")
    for key in REFS:
        regex = (
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
            if key == "namespace_id"
            else r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}"
        )
        if type(value[key]) is not str or not re.fullmatch(regex, value[key]):
            fail("unavailable_storage")
    computers = value["allowed_computer_uuids"]
    if not isinstance(computers, list) or not 1 <= len(computers) <= 64:
        fail("unavailable_storage")
    try:
        if len(set(computers)) != len(computers):
            fail("unavailable_storage")
        for item in computers:
            uuid(item)
    except SavedResultError, TypeError:
        fail("unavailable_storage")
    return _json(canonical(value))


def load_binding(filename):
    """Snapshot one immutable local Nix config; never inspect the RO data alias."""
    if filename is None:
        return None
    try:
        p = Path(filename)
        if (
            not isinstance(filename, str)
            or not filename.startswith("/nix/store/")
            or str(p) != filename
            or ".." in p.parts
        ):
            return None
        for part in (*reversed(p.parents), p):
            if part.is_symlink():
                return None
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        fd = os.open(p, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
                or info.st_size > 16384
            ):
                return None
            raw = stream.read(16385)
        if len(raw) > 16384:
            return None
        return validate_binding(_json(raw))
    except OSError, ValueError, TypeError, AttributeError, SavedResultError:
        return None


def _selection_matches(actual, selection):
    if not same(actual, selection):
        fail("identity_mismatch")


def _dto_guard(function):
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KeyError, IndexError, TypeError, AttributeError, UnicodeError, RecursionError:
            fail()

    return guarded


@_dto_guard
def catalogue(adapter, selection):
    result = adapter.service.get_saved_sources(
        selection["run_uuid"],
        selection["execution_id"],
        attempt=selection["attempt"],
        calcjob_uuid=selection["calcjob_uuid"],
    )
    result = _json(_bytes(result))
    _selection_matches(result.get("selection"), selection)
    rows = result.get("sources")
    if result.get("complete") is not True or not isinstance(rows, list):
        fail()
    if len(rows) > 256:
        fail("budget_exceeded")
    sources = []
    seen = set()
    for row in rows:
        _selection_matches(row.get("selection"), selection)
        source = stored_uuid(row.get("source_uuid"))
        digest(row.get("source_sha256"))
        if (
            source in seen
            or row.get("status") != "available"
            or row.get("metadata_status") != "verified"
            or "copy_status" not in row
        ):
            fail()
        seen.add(source)
        sources.append(
            {
                k: row[k]
                for k in (
                    "status",
                    "source_uuid",
                    "source_sha256",
                    "selection",
                    "metadata_status",
                    "copy_status",
                )
            }
            | {"access_status": "not_checked"}
        )
    return dict(
        selection=selection,
        provider_selection=selection,
        provider="nfs_registered",
        sources=sources,
        complete=True,
        storage_binding_status="configured" if adapter.binding else "unavailable",
    )


@_dto_guard
def retained(dto, selection, source):
    dto = _json(_bytes(dto))
    if not isinstance(dto, dict):
        fail()
    _selection_matches(dto.get("selection"), selection)
    if (
        dto.get("source_uuid") != source["source_uuid"]
        or dto.get("source_sha256") != source["source_sha256"]
    ):
        fail("identity_mismatch")
    if (
        dto.get("status") != "available"
        or dto.get("metadata_status") != "verified"
        or dto.get("access_status") != "not_checked"
    ):
        fail()
    record = dto.get("record")
    metadata = dto.get("metadata")
    plan = dto.get("plan")
    if (
        not isinstance(record, dict)
        or record.get("schema") != "qcl-negf.aiida-saved-source.v1"
        or sha(canonical(record)) != source["source_sha256"]
    ):
        fail("identity_mismatch")
    required_record = {
        "schema",
        "selection",
        "plan",
        "storage",
        "snapshot",
        "points",
        "metadata",
        "integrity",
        "copy_status",
        "request_uuid",
        "request_sha256",
        "registration_source_id",
    }
    if set(record) != required_record:
        fail()
    storage = record.get("storage")
    if not isinstance(storage, dict) or set(storage) != {
        *REFS,
        "computer_uuid",
        "remote_folder_uuid",
        "relative_result_root",
    }:
        fail()
    stored_uuid(storage["computer_uuid"])
    stored_uuid(storage["remote_folder_uuid"])
    path(storage["relative_result_root"])
    _selection_matches(record.get("selection"), selection)
    if not same(dto.get("copy_status"), record.get("copy_status")):
        fail()
    if not isinstance(plan, str) or not isinstance(metadata, dict):
        fail()
    plan_raw = plan.encode("utf-8")
    if len(plan_raw) > METADATA:
        fail("budget_exceeded")
    if sha(plan_raw) != record["plan"]["sha256"]:
        fail("identity_mismatch")
    plan_obj = _json(plan_raw)
    if plan_obj.get("fingerprint") != record["plan"]["plan_fingerprint"]:
        fail("identity_mismatch")
    descriptors = record.get("metadata")
    points = record.get("points")
    if not isinstance(descriptors, list) or not isinstance(points, list):
        fail()
    if len(points) > 256 or len(descriptors) > 1024:
        fail("budget_exceeded")
    names = set()
    total = 0
    raws = {}
    for item in descriptors:
        name = path(item["path"], reserved=True)
        if name in names or not isinstance(metadata.get(name), str):
            fail()
        names.add(name)
        raw = metadata[name].encode("utf-8")
        total += len(raw)
        if total > METADATA:
            fail("budget_exceeded")
        if (
            type(item.get("bytes")) is not int
            or item["bytes"] != len(raw)
            or sha(raw) != item.get("sha256")
        ):
            fail()
        _json(raw)
        raws[name] = raw
    if set(metadata) != names:
        fail()
    snapshot = record["snapshot"]
    if snapshot.get("metadata_set_sha256") != sha(canonical(descriptors)):
        fail()
    series = raws.get("series_result.json")
    if (
        series is None
        or type(snapshot.get("result_bytes")) is not int
        or not 0 < snapshot["result_bytes"] <= METADATA
        or len(series) != snapshot["result_bytes"]
        or sha(series) != snapshot.get("result_sha256")
    ):
        fail()
    if (
        type(snapshot.get("complete")) is not bool
        or snapshot.get("publication_state") != "closed_registered"
    ):
        fail()
    if record.get("integrity") != {
        "metadata": "verified",
        "payload": "not_measured",
        "payload_receipt_ref": None,
    }:
        fail("unsupported_contract")
    series_obj = _json(series)
    rows = {}
    for row in series_obj.get("points", []):
        if row["id"] in rows:
            fail()
        rows[row["id"]] = row
    frozen = {
        point["id"]
        for point in plan_obj["points"]
        if point["execution_id"] == selection["execution_id"]
    }
    captured = {}
    for point in points:
        name = point.get("point_id")
        if not isinstance(name, str) or name in captured:
            fail()
        if name not in frozen:
            fail("identity_mismatch")
        relative = path(point["commit_path"])
        raw = raws.get(relative)
        if raw is None or sha(raw) != point.get("commit_sha256"):
            fail("identity_mismatch")
        commit = _json(raw)
        identity = point["native_identity"]
        if not isinstance(identity, dict) or not same(identity, commit.get("identity")):
            fail("identity_mismatch")
        for key, wanted in {
            "point_id": name,
            "execution_id": selection["execution_id"],
            "attempt": point["source_attempt"],
            "plan_fingerprint": plan_obj["fingerprint"],
        }.items():
            if not same(identity.get(key), wanted):
                fail("identity_mismatch")
        if (
            type(point["source_attempt"]) is not int
            or type(point["row_attempt"]) is not int
            or not 1 <= point["source_attempt"] <= point["row_attempt"] <= selection["attempt"]
        ):
            fail("identity_mismatch")
        if point.get("source_calcjob_uuid") is not None:
            stored_uuid(point["source_calcjob_uuid"])
        row = rows.get(name, {})
        if row.get("data", {}).get("result_commit") != relative or not same(
            row.get("attempt"), point["row_attempt"]
        ):
            fail("identity_mismatch")
        if (
            row.get("data", {}).get("source_commit_sha256", point["commit_sha256"])
            != point["commit_sha256"]
        ):
            fail("identity_mismatch")
        source_attempt = row.get("data", {}).get("checkpoint_source_attempt", row.get("attempt"))
        if not same(source_attempt, point["source_attempt"]):
            fail("identity_mismatch")
        # Validate declared closure without opening any NFS path.
        closure, owner = declarations(commit, relative)
        actual = (
            None
            if owner is None
            else dict(
                path=str(Path(relative).parent / owner.path),
                bytes=owner.size,
                sha256=owner.sha256,
                schema=owner.schema,
            )
        )
        if not same(actual, point.get("declared_analysis")):
            fail("identity_mismatch")
        captured[name] = point
    if snapshot["complete"] and set(captured) != frozen:
        fail("identity_mismatch")
    return record, raws, plan_raw, series, plan_obj, series_obj, captured


def declarations(commit, relative):
    from qcl_negf_contracts.artifacts import validate_commit

    declared = validate_commit(commit)
    bypath = {d.path: d for d in declared}
    owners = [d for d in declared if d.role == "physics.analysis"]
    if len(owners) > 1:
        fail("unsupported_contract")
    for d in declared:
        path(d.path)
    if not owners:
        return [], None
    owner = owners[0]
    if owner.schema != "qcl-negf-physics-analysis-v4":
        fail("unsupported_contract")
    if owner.size > OWNER:
        fail("budget_exceeded")
    closure = {owner.path}
    pending = list(owner.dependencies)
    total = 0
    while pending:
        name = path(pending.pop())
        if name in closure:
            continue
        dep = bypath[name]
        if dep.media_type != "application/json":
            fail("unsupported_contract")
        total += dep.size
        if total > METADATA:
            fail("budget_exceeded")
        closure.add(name)
        pending.extend(dep.dependencies)
    parent = Path(relative).parent
    return [path(str(parent / name)) for name in closure], owner


def _cursor(query, digest, total):
    offset = 0
    if query.get("cursor") is not None:
        try:
            pin, number = query["cursor"].split(":")
            offset = int(number)
            if (
                pin != digest
                or str(offset) != number
                or offset < 0
                or offset > total
                or offset % 256
            ):
                fail("identity_mismatch")
        except ValueError, TypeError, AttributeError:
            fail("identity_mismatch")
    if query.get("metadata_sha256", digest) != digest:
        fail("identity_mismatch")
    return offset


def perform(adapter, operation, selection, source, query, cancelled, deadline, check):
    dto = adapter.service.get_saved_source(
        selection["run_uuid"],
        selection["execution_id"],
        attempt=selection["attempt"],
        calcjob_uuid=selection["calcjob_uuid"],
        source_uuid=source["source_uuid"],
        expected_source_sha256=source["source_sha256"],
    )
    check()
    record, raws, plan_raw, series_raw, plan, series, captured = retained(dto, selection, source)

    def frame(raw):
        return len(raw).to_bytes(8, "big") + raw

    metadata_sha = sha(
        b"qcl-negf.portal-saved-nfs.v1\0"
        + frame(canonical(selection))
        + frame(canonical(source))
        + frame(plan_raw)
        + frame(series_raw)
    )
    brief = adapter.brief(
        dict(
            plan=plan,
            series=series,
            plan_source="aiida.input.plan",
            source=dict(kind="nfs_registered", locator_id=source["source_uuid"]),
        ),
        selection=selection,
        maximum_bytes=METADATA,
    )
    if len(_bytes(brief)) > METADATA:
        fail("budget_exceeded")
    offset = _cursor(query, metadata_sha, len(brief["points"]))
    common = dict(
        provider_selection=selection,
        provider_source=source,
        metadata_sha256=metadata_sha,
        storage_binding_status="binding_configured_access_not_checked"
        if adapter.binding
        else "binding_unavailable",
        snapshot_complete=record["snapshot"]["complete"],
        registered_point_ids=list(captured),
        repository_copied_bytes=0,
        payload_temp_copied_bytes=0,
        provider_measurements=dict(kernel_io="not_measured", rss="not_measured"),
        metadata_status="verified",
        copy_status=dto["copy_status"],
        access_status="not_checked",
    )
    if operation == "brief":
        page = [dict(p) for p in brief["points"][offset : offset + 256]]
        for point in page:
            pin = captured.get(point["point_id"])
            point.update(
                source_commit_sha256=pin["commit_sha256"] if pin else None,
                source_commit_sha256_source="registered_sha256" if pin else None,
                analysis_source_status="unregistered"
                if pin is None
                else "registered"
                if pin["declared_analysis"]
                else "owner_unavailable",
                source_native_identity=pin["native_identity"] if pin else None,
                source_calcjob_uuid=pin["source_calcjob_uuid"] if pin else None,
            )
        ids = {p["point_id"] for p in page}
        return {
            **brief,
            **common,
            "points": page,
            "current_segments": [
                [p for p in s if p in ids]
                for s in brief["current_segments"]
                if any(p in ids for p in s)
            ],
            "total": len(brief["points"]),
            "complete_page": offset == 0 and len(page) == len(brief["points"]),
            "next_cursor": f"{metadata_sha}:{offset + len(page)}"
            if offset + len(page) < len(brief["points"])
            else None,
        }
    point = captured.get(query["point_id"])
    if point is None or point["declared_analysis"] is None:
        fail("unavailable_storage")
    if point["commit_sha256"] != query["source_commit_sha256"]:
        fail("identity_mismatch")
    binding = adapter.binding
    if binding is None:
        fail("unavailable_storage")
    storage = record["storage"]
    if (
        any(not same(storage.get(k), binding[k]) for k in REFS)
        or storage.get("computer_uuid") not in binding["allowed_computer_uuids"]
    ):
        fail("unavailable_storage")
    relative = path(storage["relative_result_root"])
    request = dict(
        provider_source=source,
        binding=binding,
        binding_sha256=sha(canonical(binding)),
        relative_result_root=relative,
        data_root=ROOT + "/" + relative,
        commit_path=point["commit_path"],
        commit_metadata=raws[point["commit_path"]].decode(),
        expected_identity={
            **point["native_identity"],
            "source_commit_sha256": point["commit_sha256"],
        },
    )
    if operation == "slice":
        request.update(view_id=query["view_id"], selection=query["selection"])
    spool = tempfile.TemporaryDirectory(prefix="qcl-saved-nfs-", dir="/tmp")
    try:
        check()
        result = adapter.worker(operation, request, Path(spool.name), cancelled, deadline)
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
    return {
        **result,
        **common,
        "source_attempt": point["source_attempt"],
        "point_id": point["point_id"],
        "source_native_identity": point["native_identity"],
        "source_calcjob_uuid": point["source_calcjob_uuid"],
        "request_spool_bytes": len(_bytes({"operation": operation, **request})),
        "total": len(result.get("views", [])),
        "complete_page": True,
        "next_cursor": None,
    }


def _unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def _access(alias, relative, paths, *, mountinfo=None):
    """Child-only local mountinfo + narrow requested ancestry checks."""
    root = Path(alias)
    for ancestor in (*reversed(root.parents), root):
        if ancestor.is_symlink():
            fail("unavailable_storage")
    if mountinfo is None:
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            mountinfo = stream.read(1024 * 1024 + 1)
    if len(mountinfo.encode()) > 1024 * 1024:
        fail("budget_exceeded")
    mounts = []
    for line in mountinfo.splitlines():
        try:
            head, tail = line.split(" - ", 1)
            a = head.split()
            b = tail.split()
            mounts.append(
                dict(
                    device=a[2],
                    root=_unescape(a[3]),
                    mount=_unescape(a[4]),
                    options=a[5].split(","),
                    fs=b[0],
                    source=_unescape(b[1]),
                )
            )
        except ValueError, IndexError:
            fail("unavailable_storage")
    aliases = [m for m in mounts if m["mount"] == alias]
    if len(aliases) != 1:
        fail("unavailable_storage")
    admitted = aliases[0]
    if admitted["fs"] != "nfs4" or "ro" not in admitted["options"] or "rw" in admitted["options"]:
        fail("unavailable_storage")
    relative = path(relative)
    result = root / relative
    targets = [result / path(p) for p in paths]
    for target in [result, *targets]:
        for ancestor in (*reversed(target.parents), target):
            if ancestor.is_symlink():
                fail("unavailable_storage")
        for mount in mounts:
            mp = Path(mount["mount"])
            if mp != root and root in mp.parents and (mp == target or mp in target.parents):
                expected = str(Path(admitted["root"]) / mp.relative_to(root))
                if (
                    mount["device"] != admitted["device"]
                    or mount["source"] != admitted["source"]
                    or mount["fs"] != "nfs4"
                    or "ro" not in mount["options"]
                    or "rw" in mount["options"]
                    or mount["root"] != expected
                ):
                    fail("unavailable_storage")
    return result


def child_access(request):
    allowed = {
        "operation",
        "provider_source",
        "binding",
        "binding_sha256",
        "relative_result_root",
        "data_root",
        "commit_path",
        "commit_metadata",
        "expected_identity",
    }
    if request.get("operation") == "slice":
        allowed |= {"view_id", "selection"}
    elif request.get("operation") != "manifest":
        fail("unsupported_contract")
    if set(request) != allowed:
        fail("selection_required")
    expected = request["expected_identity"]
    if not isinstance(expected, dict) or not {
        "point_id",
        "execution_id",
        "attempt",
        "plan_fingerprint",
        "source_commit_sha256",
    } <= set(expected):
        fail("selection_required")
    if type(expected["attempt"]) is not int or expected["attempt"] < 1:
        fail("selection_required")
    digest(expected["plan_fingerprint"])
    digest(expected["source_commit_sha256"])
    if request["operation"] == "slice":
        if not isinstance(request["view_id"], str) or not re.fullmatch(
            r"[A-Za-z0-9_]{1,128}", request["view_id"]
        ):
            fail("selection_required")
        selection = request["selection"]
        if not isinstance(selection, list) or not 1 <= len(selection) <= 2:
            fail("selection_required")
        for item in selection:
            if type(item) is int and item >= 0:
                continue
            if (
                not isinstance(item, dict)
                or set(item) != {"start", "stop", "step"}
                or any(type(v) is not int for v in item.values())
                or not 0 <= item["start"] < item["stop"]
                or item["step"] < 1
            ):
                fail("selection_required")
    binding = validate_binding(request["binding"])
    if sha(canonical(binding)) != request["binding_sha256"]:
        fail("identity_mismatch")
    source, extra = provider(request["provider_source"])
    if extra or source["provider"] != "nfs_registered":
        fail("selection_required")
    relative = path(request["relative_result_root"])
    if request["data_root"] != ROOT + "/" + relative:
        fail("identity_mismatch")
    commit_path = path(request["commit_path"])
    raw = request["commit_metadata"].encode("utf-8")
    if len(raw) > METADATA:
        fail("budget_exceeded")
    if sha(raw) != request["expected_identity"]["source_commit_sha256"]:
        fail("identity_mismatch")
    closure, owner = declarations(_json(raw), commit_path)
    if owner is None:
        fail("unsupported_contract")
    return _access(binding["root"], relative, [commit_path, *closure])
