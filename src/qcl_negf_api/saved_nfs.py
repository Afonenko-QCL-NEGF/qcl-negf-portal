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

from .saved_results import METADATA, SavedResultError, _bytes, _json, _path

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


# Native5 retained-point transport. Legacy registration/closure functions above
# are not a native reader and are not dispatched by SavedResults.
def _native_query(operation, query):
    required = {"provider", "point_id", "native_binding_uuid", "state_manifest_sha256"}
    extra = (
        {"view_id", "selection", "source_state"}
        if operation == "slice"
        else {"stage", "fields", "maximum_records", "source_state", "cursor"}
        if operation == "history"
        else set()
    )
    if (
        operation not in {"manifest", "diagnostics", "slice", "history"}
        or set(query) != required | extra
    ):
        fail("selection_required")
    if query["provider"] != "native_nfs":
        fail("unsupported_contract")
    if not isinstance(query["point_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", query["point_id"]
    ):
        fail("selection_required")
    uuid(query["native_binding_uuid"])
    try:
        digest(query["state_manifest_sha256"])
    except SavedResultError:
        fail("selection_required")
    if operation == "slice":
        if not isinstance(query["view_id"], str) or not re.fullmatch(
            r"[A-Za-z0-9_]{1,128}", query["view_id"]
        ):
            fail("selection_required")
        if not isinstance(query["selection"], list) or not 1 <= len(query["selection"]) <= 2:
            fail("selection_required")
        for item in query["selection"]:
            if type(item) is int and 0 <= item <= 9007199254740991:
                continue
            if (
                type(item) is not dict
                or set(item) != {"start", "stop", "step"}
                or any(type(v) is not int or not 0 <= v <= 9007199254740991 for v in item.values())
                or item["start"] >= item["stop"]
                or item["step"] < 1
            ):
                fail("selection_required")
    if operation == "history":
        if (
            query["stage"] not in ("scba", "outer")
            or not isinstance(query["fields"], list)
            or not 1 <= len(query["fields"]) <= 16
            or any(not isinstance(v, str) or not v or len(v) > 128 for v in query["fields"])
            or len(set(query["fields"])) != len(query["fields"])
            or type(query["maximum_records"]) is not int
            or not 1 <= query["maximum_records"] <= 256
            or (query["cursor"] is not None and type(query["cursor"]) is not dict)
        ):
            fail("selection_required")
    if operation in ("slice", "history") and type(query["source_state"]) is not dict:
        fail("selection_required")
    return dict(query)


def _native_point(dto, selection, query):
    """Check detached original owner relations; this never grants data access."""
    from qcl_negf_contracts import schema_validator

    try:
        if (
            type(dto) is not dict
            or set(dto)
            != {
                "status",
                "point",
                "binding",
                "result_utf8",
                "native_result",
                "native_binding",
                "integrity",
            }
            or dto["status"] != "available"
        ):
            fail("unsupported_contract")
        _bytes(dto)
        binding, point = dto["binding"], dto["point"]
        if (
            binding.get("schema") != "qcl-negf.aiida-native-binding.v1"
            or not same(binding["selection"], selection)
            or point["point_id"] != query["point_id"]
            or dto["native_binding"]["node_uuid"] != query["native_binding_uuid"]
            or [p for p in binding["points"] if p["point_id"] == query["point_id"]] != [point]
        ):
            fail("identity_mismatch")
        for key in ("native_result", "native_binding"):
            pin = dto[key]
            if type(pin) is not dict or set(pin) != {"node_uuid", "bytes", "sha256"}:
                fail()
            stored_uuid(pin["node_uuid"])
            digest(pin["sha256"])
            if not isinstance(pin["bytes"], str) or not re.fullmatch(
                r"0|[1-9][0-9]{0,19}", pin["bytes"]
            ):
                fail()
        raw = dto["result_utf8"].encode("utf-8")
        raw_ref = dict(bytes=str(len(raw)), sha256=sha(raw))
        if raw_ref != binding["raw_result"] or any(
            raw_ref[k] != dto["native_result"][k] for k in raw_ref
        ):
            fail("identity_mismatch")
        if dto["integrity"] != dict(
            native_contexts="not_reverified",
            current_access="not_checked",
            scientific_acceptance="not_assessed",
        ):
            fail()
        if binding["integrity"] != dict(
            scope="selected_metadata",
            native_contexts="verified",
            transport_owner="verified",
            full_core_payloads="not_assessed",
            current_access="not_checked",
        ):
            fail("unsupported_contract")
        source = point["source"]
        validator = schema_validator("outward-result.schema.json")
        validator.evolve(
            schema={"$ref": "#/$defs/NativePointSource", "$defs": validator.schema["$defs"]}
        ).validate(source)
        if source["kind"] == "absent":
            fail("capability_unavailable")
        state = source["selected_state"]
        if (
            state["identity"]["execution_id"] != selection["execution_id"]
            or state["identity"]["point_id"] != query["point_id"]
        ):
            fail("identity_mismatch")
        if state["manifest"]["sha256"] != query["state_manifest_sha256"] or (
            "source_state" in query and not same(query["source_state"], state)
        ):
            fail("identity_mismatch")
        return point, source
    except SavedResultError:
        raise
    except (KeyError, IndexError, TypeError, AttributeError, UnicodeError, ValueError) as exc:
        raise SavedResultError("corrupt_result") from exc
    except Exception as exc:
        raise SavedResultError("corrupt_result") from exc


def native_perform(adapter, operation, selection, query, cancelled, deadline, check):
    query = _native_query(operation, dict(query))
    method = getattr(adapter.service, "get_native_result_source", None)
    if not callable(method):
        fail("unsupported_contract")
    dto = method(
        selection["run_uuid"],
        selection["execution_id"],
        attempt=selection["attempt"],
        calcjob_uuid=selection["calcjob_uuid"],
        point_id=query["point_id"],
        binding_uuid=query["native_binding_uuid"],
    )
    check()
    point, source = _native_point(dto, selection, query)
    binding = adapter.binding
    if binding is None:
        fail("unavailable_storage")
    storage = dto["binding"]["storage"]
    if (
        any(storage.get(k) != binding[k] for k in REFS)
        or storage.get("computer_uuid") not in binding["allowed_computer_uuids"]
    ):
        fail("unavailable_storage")
    relative = path(storage["relative_execution_root"])
    artifact = source["artifact_root"]
    if artifact != ".":
        relative = path(relative + "/" + path(artifact))
    request = dict(
        binding=binding,
        binding_sha256=sha(canonical(binding)),
        relative_root=relative,
        state_ref=source["selected_state"],
        source=source,
        query=query,
    )
    spool = tempfile.TemporaryDirectory(prefix="qcl-native-selected-")
    primary = None
    try:
        check()
        result = adapter.worker(operation, request, Path(spool.name), cancelled, deadline)
        check()
        answer = dict(
            schema="qcl-negf.portal-selected-reader.v2",
            operation=operation,
            provider_selection=selection,
            provider="native_nfs",
            selection_mode="retained",
            source_state=source["selected_state"],
            retained=dict(
                native_result=dto["native_result"],
                native_binding=dto["native_binding"],
                creator=point["creator"],
                integrity=dict(
                    readback=dto["integrity"], original_binding=dto["binding"]["integrity"]
                ),
            ),
            access=result["access"],
            view=result["view"],
        )
        _bytes(answer)
        return answer
    except BaseException as exc:
        primary = exc
        if getattr(exc, "cleanup_unconfirmed", False):
            adapter.closed = adapter.lease_cleanup_unconfirmed = True
            adapter.pending_spools.append(spool)
            spool = None
        raise
    finally:
        if spool is not None:
            try:
                spool.cleanup()
            except OSError as exc:
                adapter.closed = adapter.lease_cleanup_unconfirmed = True
                adapter.pending_spools.append(spool)
                if primary is not None:
                    primary.cleanup_unconfirmed = True
                else:
                    error = SavedResultError("unavailable_storage")
                    error.cleanup_unconfirmed = True
                    raise error from exc


def _mountinfo():
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw.encode()) > 1024 * 1024:
        fail("budget_exceeded")
    return raw


class _NativeDomain:
    """Actual child-owned nofollow ancestry FDs and immutable mount domain."""

    def __init__(self, binding, relative):
        self.fds = []
        self.mounts = _mountinfo()
        self.namespace = os.stat("/proc/self/ns/mnt").st_ino
        self.root = _access(binding["root"], relative, [], mountinfo=self.mounts)
        alias = Path(binding["root"])
        for line in self.mounts.splitlines():
            try:
                mp = Path(_unescape(line.split(" - ", 1)[0].split()[4]))
            except IndexError, ValueError:
                fail("unavailable_storage")
            if (
                mp != alias
                and alias in mp.parents
                and (mp == self.root or self.root in mp.parents or mp in self.root.parents)
            ):
                fail("unavailable_storage")
        try:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.fds.append((Path("/"), fd, self._stamp(os.fstat(fd))))
            current = Path("/")
            for part in self.root.parts[1:]:
                fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
                current = current / part
                self.fds.append((current, fd, self._stamp(os.fstat(fd))))
            self.check()
        except BaseException as primary:
            try:
                self.close()
            except BaseException:
                primary.cleanup_unconfirmed = True
            raise

    @staticmethod
    def _stamp(info):
        return info.st_dev, info.st_ino, info.st_mode

    def check(self):
        try:
            if _mountinfo() != self.mounts or os.stat("/proc/self/ns/mnt").st_ino != self.namespace:
                fail("source_changed")
            for p, fd, stamp in self.fds:
                if (
                    self._stamp(os.fstat(fd)) != stamp
                    or self._stamp(p.stat(follow_symlinks=False)) != stamp
                ):
                    fail("source_changed")
        except OSError as exc:
            raise SavedResultError("source_changed") from exc

    def close(self):
        error = None
        while self.fds:
            _, fd, _ = self.fds.pop()
            try:
                os.close(fd)
            except OSError as exc:
                error = exc
        if error is not None:
            result = SavedResultError("unavailable_storage")
            result.cleanup_unconfirmed = True
            raise result from error


def native_read_request(request):
    """Only the existing isolated child opens native bytes; owners close here."""
    from dataclasses import asdict

    from qcl_negf_results.diagnostic_views import (
        HistoryViewRequest,
        describe_saved_diagnostics,
        read_saved_history,
    )
    from qcl_negf_results.saved_views import describe_saved_views, read_saved_view
    from qcl_negf_results.state import ResultReadLimits, StateReader

    if set(request) != {
        "operation",
        "binding",
        "binding_sha256",
        "relative_root",
        "state_ref",
        "source",
        "query",
    }:
        fail("selection_required")
    operation = request["operation"]
    query = _native_query(operation, request["query"])
    binding = validate_binding(request["binding"])
    if sha(canonical(binding)) != request["binding_sha256"]:
        fail("identity_mismatch")
    source = request["source"]
    state = request["state_ref"]
    if (
        not same(source["selected_state"], state)
        or state["manifest"]["sha256"] != query["state_manifest_sha256"]
        or ("source_state" in query and not same(query["source_state"], state))
    ):
        fail("identity_mismatch")
    relative = path(request["relative_root"])
    domain = None
    primary = None
    try:
        domain = _NativeDomain(binding, relative)
        limits = ResultReadLimits(
            maximum_metadata_bytes=METADATA,
            maximum_io_bytes=128 * 1024 * 1024,
            maximum_catalog_pages=4096,
            maximum_catalog_entries=4096,
            maximum_catalog_bytes=METADATA,
            maximum_open_files=4,
            maximum_batch_bytes=METADATA,
            maximum_record_bytes=METADATA,
            maximum_batch_records=256,
            maximum_hdf_objects=4096,
            maximum_array_bytes=METADATA,
        )
        with StateReader(
            domain.root, state_ref=state, expected_identity=state["identity"], limits=limits
        ) as reader:
            if operation in ("manifest", "slice"):
                if source["kind"] != "final" or source["observations"] is None:
                    fail("capability_unavailable")
                if operation == "manifest":
                    view = describe_saved_views(reader, artifact_ref=source["observations"])
                else:
                    view = read_saved_view(
                        reader,
                        query["view_id"],
                        query["selection"],
                        artifact_ref=source["observations"],
                    )
            elif operation == "diagnostics":
                view = describe_saved_diagnostics(reader)
            else:
                reader.import_history()
                view = asdict(
                    read_saved_history(
                        reader,
                        HistoryViewRequest(
                            query["stage"], tuple(query["fields"]), query["maximum_records"]
                        ),
                        cursor=query["cursor"],
                    )
                )
        # No owner handle escapes. A changed mount/root after close refuses bytes.
        domain.check()
        result = dict(
            view=view,
            access=dict(
                status="checked_ro_current_request",
                method="local_binding_mountinfo_nofollow_domain",
                config_sha256=request["binding_sha256"],
            ),
        )
        _bytes(result)
        return result
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if domain is not None:
            try:
                domain.close()
            except BaseException:
                if primary is not None:
                    primary.cleanup_unconfirmed = True
                else:
                    raise
