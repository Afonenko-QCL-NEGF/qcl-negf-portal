"""Registered-source transport only; native semantics belong to Results/AiiDA.

No root access occurs outside child_access. Binding startup reads local config only.
"""

import base64
import hashlib
import json
import math
import os
import re
import stat
import struct
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
    required = (
        {"point_id"}
        if operation == "point-source"
        else {"provider", "point_id", "native_binding_uuid", "state_manifest_sha256"}
    )
    extra = (
        {"view_id", "selection", "source_state"}
        if operation == "slice"
        else {"stage", "fields", "maximum_records", "source_state", "cursor"}
        if operation == "history"
        else set()
    )
    if (
        operation not in {"point-source", "manifest", "diagnostics", "slice", "history"}
        or set(query) != required | extra
    ):
        fail("selection_required")
    if operation != "point-source" and query["provider"] != "native_nfs":
        fail("unsupported_contract")
    if not isinstance(query["point_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", query["point_id"]
    ):
        fail("selection_required")
    if operation == "point-source":
        return dict(query)
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


def _native_point(dto, selection, query, *, metadata=False):
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
            or (not metadata and dto["native_binding"]["node_uuid"] != query["native_binding_uuid"])
            or [p for p in binding["points"] if p["point_id"] == query["point_id"]] != [point]
        ):
            fail("identity_mismatch")
        if (
            set(point)
            != {
                "point_id",
                "point_attempt",
                "source",
                "source_invocation",
                "creator",
                "plan_point_id",
                "coordinates",
            }
            or point["plan_point_id"] != query["point_id"]
            or type(point["point_attempt"]) is not int
            or not 1 <= point["point_attempt"] <= 9007199254740991
            or not 1 <= selection["attempt"] <= 9007199254740991
        ):
            fail("corrupt_result")
        creator = point["creator"]
        if point["source"]["kind"] == "absent":
            if creator is not None or point["source_invocation"] is not None:
                fail("corrupt_result")
        else:
            if type(creator) is not dict or set(creator) != {"calcjob_uuid", "binding_uuid"}:
                fail("corrupt_result")
            stored_uuid(creator["calcjob_uuid"])
            if creator["binding_uuid"] is not None:
                stored_uuid(creator["binding_uuid"])
            if (creator["calcjob_uuid"] == selection["calcjob_uuid"]) != (
                creator["binding_uuid"] is None
            ):
                fail("identity_mismatch")
        for key in ("native_result", "native_binding"):
            pin = dto[key]
            if type(pin) is not dict or set(pin) != {"node_uuid", "bytes", "sha256"}:
                fail()
            stored_uuid(pin["node_uuid"])
            digest(pin["sha256"])
            if (
                not isinstance(pin["bytes"], str)
                or not re.fullmatch(r"0|[1-9][0-9]{0,19}", pin["bytes"])
                or int(pin["bytes"]) > 18446744073709551615
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
            if metadata:
                return point, source
            fail("capability_unavailable")
        state = source["selected_state"]
        _path(state["manifest"]["path"])
        if (
            state["identity"]["execution_id"] != selection["execution_id"]
            or state["identity"]["point_id"] != query["point_id"]
        ):
            fail("identity_mismatch")
        if not metadata and (
            state["manifest"]["sha256"] != query["state_manifest_sha256"]
            or ("source_state" in query and not same(query["source_state"], state))
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
    if operation in {"derive-history", "compare-history"}:
        return _history_perform(adapter, operation, selection, query, cancelled, deadline, check)
    if operation in {"sources", "brief"}:
        return _catalog_perform(adapter, operation, selection, query, cancelled, deadline, check)
    query = _native_query(operation, dict(query))
    if operation == "point-source" and (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", selection["execution_id"])
        or selection["attempt"] > 9007199254740991
    ):
        fail("selection_required")
    method = getattr(adapter.service, "get_native_result_source", None)
    if not callable(method):
        fail("unsupported_contract")
    dto = method(
        selection["run_uuid"],
        selection["execution_id"],
        attempt=selection["attempt"],
        calcjob_uuid=selection["calcjob_uuid"],
        point_id=query["point_id"],
        binding_uuid=None if operation == "point-source" else query["native_binding_uuid"],
    )
    check()
    point, source = _native_point(dto, selection, query, metadata=operation == "point-source")
    retained = dict(
        native_result=dto["native_result"],
        native_binding=dto["native_binding"],
        creator=point["creator"],
        integrity=dict(readback=dto["integrity"], original_binding=dto["binding"]["integrity"]),
    )
    if operation == "point-source":
        answer = dict(
            schema="qcl-negf.portal-point-source.v1",
            status=dto["status"],
            scope="original_owner_metadata",
            provider_selection=selection,
            point={k: point[k] for k in ("point_id", "point_attempt", "plan_point_id")},
            source={k: source[k] for k in ("kind", "relation", "reason")},
            source_state=source["selected_state"],
            retained=retained,
        )
        _bytes(answer)
        check()
        return answer
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
            retained=retained,
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
    if request.get("operation") in {"derive-history", "compare-history"}:
        return _history_read(request)
    if request.get("operation") == "brief":
        return _brief_page_read(request)
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


PAGE_SCHEMA = "qcl-negf.portal-native-page.v1"
PAGE_IO = 128 * 1024 * 1024


def _node_pin(value):
    if type(value) is not dict or set(value) != {"node_uuid", "bytes", "sha256"}:
        fail()
    stored_uuid(value["node_uuid"])
    digest(value["sha256"])
    if (
        type(value["bytes"]) is not str
        or not re.fullmatch(r"0|[1-9][0-9]{0,19}", value["bytes"])
        or int(value["bytes"]) > 18446744073709551615
    ):
        fail()


def _page_decode(token):
    """A bounded exact selector, revalidated against original pins on every call."""
    try:
        if (
            type(token) is not str
            or not 1 <= len(token) <= 2731
            or not re.fullmatch(r"[A-Za-z0-9_-]+", token)
        ):
            fail("selection_required")
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        if len(raw) > 2048:
            fail("selection_required")
        value = _json(raw)
        if (
            type(value) is not dict
            or set(value)
            != {
                "schema",
                "provider_selection",
                "native_result",
                "native_binding",
                "input_plan",
                "source_graph",
                "offset",
                "limit",
            }
            or value["schema"] != PAGE_SCHEMA
            or type(value["offset"]) is not str
            or not re.fullmatch(r"0|[1-9][0-9]{0,19}", value["offset"])
            or int(value["offset"]) > 18446744073709551615
            or type(value["limit"]) is not int
            or not 1 <= value["limit"] <= 8
        ):
            fail("selection_required")
        from .saved_results import _selection

        _selection(value["provider_selection"])
        for k in ("native_result", "native_binding", "input_plan", "source_graph"):
            _node_pin(value[k])
        if _page_encode(value) != token:
            fail("selection_required")
        return value
    except (ValueError, TypeError, KeyError, SavedResultError) as exc:
        raise SavedResultError("selection_required") from exc


def _page_encode(value):
    raw = canonical(value)
    if len(raw) > 2048:
        fail("budget_exceeded")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _catalog_query(operation, selection, query):
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", selection["execution_id"])
        or not 1 <= selection["attempt"] <= 9007199254740991
    ):
        fail("selection_required")
    if operation == "sources" and set(query) <= {"limit"}:
        limit = query.get("limit", 8)
        if type(limit) is not int or not 1 <= limit <= 8:
            fail("selection_required")
        return None, 0, limit
    key = "cursor" if operation == "sources" else "page_ref"
    if set(query) != {key}:
        fail("selection_required")
    token = _page_decode(query[key])
    if not same(token["provider_selection"], selection):
        fail("identity_mismatch")
    return token, int(token["offset"]), token["limit"]


def _declared_number(value):
    # Scientific-plan JSON integers have no Int64/u64/Float64 range restriction.
    if type(value) is int:
        return dict(kind="json_integer", decimal=str(value))
    if type(value) is not float or not math.isfinite(value):
        fail()
    return dict(kind="ieee", type="f64", bits=struct.pack(">d", value).hex())


def _recorded_coordinates(recorded, declared):
    # Match the owning binder: branch/order exact; recorded T/V by binary64 bits.
    if type(recorded) is not dict or set(recorded) != set(declared):
        return False
    if any(not same(recorded[k], declared[k]) for k in ("branch", "order")):
        return False
    try:
        return all(
            type(recorded[k]) in (int, float)
            and struct.pack(">d", recorded[k]) == struct.pack(">d", declared[k])
            for k in ("temperature_K", "voltage_per_period_V")
        )
    except OverflowError, struct.error:
        return False


def _catalog_dto(dto, selection):
    """One whole original metadata validation, without repeated B reads/copies."""
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
                "declared_points",
            }
            or dto["status"] != "available"
            or dto["point"] is not None
        ):
            fail("unsupported_contract")
        _bytes(dto)  # Admit the complete owner response before retained projections.
        binding = dto["binding"]
        if (
            type(binding) is not dict
            or set(binding)
            != {
                "schema",
                "selection",
                "input_plan",
                "source_graph",
                "storage",
                "raw_result",
                "points",
                "attempt_history",
                "integrity",
            }
            or binding["schema"] != "qcl-negf.aiida-native-binding.v1"
            or not same(binding["selection"], selection)
        ):
            fail("identity_mismatch")
        pins = {
            k: dto[k] if k in ("native_result", "native_binding") else binding[k]
            for k in ("native_result", "native_binding", "input_plan", "source_graph")
        }
        for pin in pins.values():
            _node_pin(pin)
        raw = dto["result_utf8"].encode("utf-8")
        raw_ref = dict(bytes=str(len(raw)), sha256=sha(raw))
        if raw_ref != binding["raw_result"] or any(
            raw_ref[k] != pins["native_result"][k] for k in raw_ref
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
        declared = dto["declared_points"]
        if type(declared) is not list or type(binding["points"]) is not list:
            fail()
        byid = {}
        for p in declared:
            if (
                type(p) is not dict
                or set(p)
                != {
                    "id",
                    "execution_id",
                    "temperature_K",
                    "voltage_per_period_V",
                    "branch",
                    "order",
                }
                or p["execution_id"] != selection["execution_id"]
                or p["id"] in byid
                or type(p["id"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", p["id"])
                or type(p["branch"]) is not str
                or type(p["order"]) is not int
                or p["order"] < 1
            ):
                fail()
            # Validate numeric types now; construct tags only for the bounded page.
            for k in ("temperature_K", "voltage_per_period_V"):
                if (
                    type(p[k]) not in (int, float)
                    or type(p[k]) is float
                    and not math.isfinite(p[k])
                ):
                    fail()
            byid[p["id"]] = p
        raw_points = _json(raw)["points"]
        if type(raw_points) is not list:
            fail()
        raw_byid = {r["id"]: r for r in raw_points}
        if len(raw_byid) != len(raw_points):
            fail()
        selected = {}
        validator = schema_validator("outward-result.schema.json")
        validator = validator.evolve(
            schema={"$ref": "#/$defs/NativePointSource", "$defs": validator.schema["$defs"]}
        )
        for p in binding["points"]:
            if (
                type(p) is not dict
                or set(p)
                != {
                    "point_id",
                    "point_attempt",
                    "source",
                    "source_invocation",
                    "creator",
                    "plan_point_id",
                    "coordinates",
                }
                or p["point_id"] in selected
                or p["point_id"] not in byid
                or p["plan_point_id"] != p["point_id"]
                or type(p["point_attempt"]) is not int
                or not 1 <= p["point_attempt"] <= 18446744073709551615
            ):
                fail()
            declared_point = byid[p["point_id"]]
            coords = {
                k: declared_point[k]
                for k in ("temperature_K", "voltage_per_period_V", "branch", "order")
            }
            row = raw_byid[p["point_id"]]
            if (
                not _recorded_coordinates(p["coordinates"], coords)
                or type(row["attempt"]) is not int
                or row["attempt"] != p["point_attempt"]
                or not same(row["coordinates"], p["coordinates"])
                or not same(row["data"]["native_source"], p["source"])
                or row.get("execution_id", selection["execution_id"]) != selection["execution_id"]
            ):
                fail("identity_mismatch")
            source = p["source"]
            validator.validate(source)
            if source["kind"] == "absent":
                if p["creator"] is not None or p["source_invocation"] is not None:
                    fail()
            else:
                state = source["selected_state"]
                _path(state["manifest"]["path"])
                if (
                    state["identity"]["point_id"] != p["point_id"]
                    or state["identity"]["execution_id"] != selection["execution_id"]
                ):
                    fail("identity_mismatch")
                creator, invocation = p["creator"], p["source_invocation"]
                if (
                    type(creator) is not dict
                    or set(creator) != {"calcjob_uuid", "binding_uuid"}
                    or type(invocation) is not dict
                    or set(invocation) != {"source_epoch"}
                    or type(invocation["source_epoch"]) is not str
                    or not invocation["source_epoch"]
                ):
                    fail()
                stored_uuid(creator["calcjob_uuid"])
                if creator["binding_uuid"] is not None:
                    stored_uuid(creator["binding_uuid"])
                if (creator["calcjob_uuid"] == selection["calcjob_uuid"]) != (
                    creator["binding_uuid"] is None
                ):
                    fail("identity_mismatch")
            if (
                any(
                    row.get(k) is not None and type(row[k]) is not str
                    for k in ("status", "quality")
                )
                or row.get("converged") is not None
                and type(row["converged"]) is not bool
            ):
                fail()
            selected[p["point_id"]] = (p, row)
        if set(selected) != set(raw_byid):
            fail("identity_mismatch")
        return binding, pins, declared, selected
    except SavedResultError:
        raise
    except Exception as exc:
        raise SavedResultError("corrupt_result") from exc


def _catalog_perform(adapter, operation, selection, query, cancelled, deadline, check):
    token, offset, limit = _catalog_query(operation, selection, query)
    method = getattr(adapter.service, "get_native_result_source", None)
    if not callable(method):
        fail("unsupported_contract")
    dto = method(
        selection["run_uuid"],
        selection["execution_id"],
        attempt=selection["attempt"],
        calcjob_uuid=selection["calcjob_uuid"],
        point_id=None,
        binding_uuid=None if token is None else token["native_binding"]["node_uuid"],
    )
    check()
    binding, pins, declared, selected = _catalog_dto(dto, selection)
    if token is not None and any(not same(token[k], v) for k, v in pins.items()):
        fail("source_changed")
    total = len(declared)
    if offset > total or total and offset == total:
        fail("selection_required")

    def cursor(at):
        return _page_encode(
            dict(
                schema=PAGE_SCHEMA,
                provider_selection=selection,
                **pins,
                offset=str(at),
                limit=limit,
            )
        )

    answer = dict(
        schema="qcl-negf.portal-native-sources.v1",
        scope="original_owner_metadata",
        provider_selection=selection,
        retained=dict(
            **pins, integrity=dict(readback=dto["integrity"], original_binding=binding["integrity"])
        ),
        pagination=dict(
            offset=str(offset),
            limit=limit,
            total=str(total),
            page_ref=cursor(offset),
            previous_cursor=cursor(max(0, offset - limit)) if offset else None,
            next_cursor=cursor(offset + limit) if offset + limit < total else None,
        ),
        points=[],
    )
    finals = []
    for d in declared[offset : offset + limit]:
        point_row = selected.get(d["id"])
        p, raw_row = point_row if point_row else (None, {})
        source = None if p is None else p["source"]
        row = dict(
            point_id=d["id"],
            declared_coordinates=dict(
                temperature_K=_declared_number(d["temperature_K"]),
                voltage_per_period_V=_declared_number(d["voltage_per_period_V"]),
                branch=d["branch"],
                order=str(d["order"]),
            ),
            row_status="not_recorded" if p is None else "recorded",
            point_attempt=None if p is None else str(p["point_attempt"]),
            source=None
            if source is None
            else {k: source[k] for k in ("kind", "relation", "reason")},
            source_state=None if source is None else source["selected_state"],
            source_invocation=None if p is None else p["source_invocation"],
            observations_ref=source["observations"]
            if source is not None and source["kind"] == "final"
            else None,
            creator=None if p is None else p["creator"],
            recorded_statuses={k: raw_row.get(k) for k in ("status", "quality", "converged")},
            missing_reason="result_row_absent" if p is None else None,
        )
        if operation == "brief":
            kind = None if source is None else source["kind"]
            row.update(
                read=dict(
                    status="not_recorded"
                    if p is None
                    else "checkpoint_only"
                    if kind == "checkpoint"
                    else "available"
                    if kind == "final"
                    else "absent",
                    reason="result_row_absent" if p is None else source["reason"],
                ),
                access=None,
                view=None,
            )
            if kind == "final":
                local = adapter.binding
                storage = binding["storage"]
                if (
                    local is None
                    or any(storage.get(k) != local[k] for k in REFS)
                    or storage.get("computer_uuid") not in local["allowed_computer_uuids"]
                ):
                    fail("unavailable_storage")
                relative = path(storage["relative_execution_root"])
                if source["artifact_root"] != ".":
                    relative = path(relative + "/" + path(source["artifact_root"]))
                finals.append(
                    dict(
                        point_id=d["id"],
                        relative_root=relative,
                        state_ref=source["selected_state"],
                        source=source,
                        source_invocation=p["source_invocation"],
                    )
                )
        answer["points"].append(row)
    _bytes(answer)
    check()
    if operation == "sources":
        return answer
    answer["schema"] = "qcl-negf.portal-native-brief.v1"
    answer["credits"] = dict(
        final_sources=0, stage_share=0, reserved_bytes=0, actual_delivered_bytes=0, epochs=[]
    )
    if not finals:
        _bytes(answer)
        return answer
    # Equal nonrefundable output slots are admitted before any root/spool read.
    allowance = (2 * 1024 * 1024 - len(_bytes(answer)) - 16384) // len(finals)
    if allowance < 2048:
        fail("budget_exceeded")
    request = dict(
        binding=adapter.binding,
        binding_sha256=sha(canonical(adapter.binding)),
        points=finals,
        output_slot=allowance,
    )
    _bytes(dict(operation="brief", **request))
    spool = tempfile.TemporaryDirectory(prefix="qcl-native-page-")
    primary = None
    try:
        check()
        result = adapter.worker("brief", request, Path(spool.name), cancelled, deadline)
        check()
        if (
            type(result) is not dict
            or set(result) != {"points", "credits"}
            or len(result["points"]) != len(finals)
        ):
            fail()
        for expected, actual in zip(finals, result["points"], strict=True):
            _brief_binding(actual, expected, request["binding_sha256"])
        _page_credits(result["credits"], finals)
        updates = {p["point_id"]: p for p in result["points"]}
        for row in answer["points"]:
            if row["point_id"] in updates:
                row.update(
                    view=updates[row["point_id"]]["view"], access=updates[row["point_id"]]["access"]
                )
        answer["credits"] = result["credits"]
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


def _brief_binding(actual, expected, config_sha256):
    if (
        type(actual) is not dict
        or set(actual) != {"point_id", "view", "access"}
        or actual["point_id"] != expected["point_id"]
    ):
        fail("identity_mismatch")
    view = actual["view"]
    if (
        type(view) is not dict
        or not {"source_state", "identity", "invocation", "artifact_ref"} <= set(view)
        or not same(view["source_state"], expected["state_ref"])
        or not same(view["identity"], expected["state_ref"]["identity"])
        or not same(view["invocation"], expected["source_invocation"])
        or not same(view["artifact_ref"], expected["source"]["observations"])
    ):
        fail("identity_mismatch")
    if actual["access"] != dict(
        status="checked_ro_current_request",
        method="local_binding_mountinfo_nofollow_domain",
        config_sha256=config_sha256,
    ):
        fail("unavailable_storage")


def _page_credits(credits, points):
    share = PAGE_IO // (2 * len(points))
    if (
        type(credits) is not dict
        or set(credits)
        != {"final_sources", "stage_share", "reserved_bytes", "actual_delivered_bytes", "epochs"}
        or credits["final_sources"] != len(points)
        or credits["stage_share"] != share
        or credits["reserved_bytes"] != 2 * len(points) * share
        or type(credits["epochs"]) is not list
        or len(credits["epochs"]) != len(points)
    ):
        fail()
    measured = 0
    for p, epoch in zip(points, credits["epochs"], strict=True):
        if (
            type(epoch) is not dict
            or set(epoch) != {"point_id", "initialization", "brief_and_close"}
            or epoch["point_id"] != p["point_id"]
        ):
            fail()
        for phase in ("initialization", "brief_and_close"):
            counter = epoch[phase]
            if (
                type(counter) is not dict
                or set(counter)
                != {
                    "bytes_read_total",
                    "sha_bytes",
                    "hdf_bytes",
                    "metadata_bytes",
                    "logical_selected_bytes",
                    "maximum_io_bytes",
                }
                or any(type(v) is not int or v < 0 for v in counter.values())
                or counter["maximum_io_bytes"] != share
                or counter["bytes_read_total"] > share
                or counter["logical_selected_bytes"] != 0
                or counter["bytes_read_total"]
                != sum(counter[k] for k in ("sha_bytes", "hdf_bytes", "metadata_bytes"))
            ):
                fail()
            measured += counter["bytes_read_total"]
    if (
        type(credits["actual_delivered_bytes"]) is not int
        or credits["actual_delivered_bytes"] != measured
    ):
        fail()


def _brief_page_read(request):
    from qcl_negf_results.saved_views import AnalysisReader, SavedViewLimits
    from qcl_negf_results.state import ResultReadLimits, StateReader

    if set(request) != {"operation", "binding", "binding_sha256", "points", "output_slot"}:
        fail("selection_required")
    binding = validate_binding(request["binding"])
    if sha(canonical(binding)) != request["binding_sha256"]:
        fail("identity_mismatch")
    points = request["points"]
    if (
        type(points) is not list
        or not 1 <= len(points) <= 8
        or type(request["output_slot"]) is not int
        or not 1024 <= request["output_slot"] <= 2 * 1024 * 1024
    ):
        fail("selection_required")
    share = PAGE_IO // (2 * len(points))
    result = dict(
        points=[],
        credits=dict(
            final_sources=len(points),
            stage_share=share,
            reserved_bytes=2 * len(points) * share,
            actual_delivered_bytes=0,
            epochs=[],
        ),
    )
    seen = set()
    for p in points:
        if (
            type(p) is not dict
            or set(p) != {"point_id", "relative_root", "state_ref", "source", "source_invocation"}
            or p["point_id"] in seen
            or p["source"]["kind"] != "final"
            or not same(p["state_ref"], p["source"]["selected_state"])
            or p["state_ref"]["identity"]["point_id"] != p["point_id"]
        ):
            fail("identity_mismatch")
        seen.add(p["point_id"])
        domain = None
        primary = None
        try:
            domain = _NativeDomain(binding, path(p["relative_root"]))
            with StateReader(
                domain.root,
                state_ref=p["state_ref"],
                expected_identity=p["state_ref"]["identity"],
                limits=ResultReadLimits(
                    maximum_metadata_bytes=METADATA,
                    maximum_io_bytes=share,
                    maximum_catalog_entries=4096,
                    maximum_catalog_bytes=METADATA,
                    maximum_open_files=4,
                    maximum_batch_bytes=METADATA,
                    maximum_record_bytes=METADATA,
                    maximum_hdf_objects=4096,
                    maximum_array_bytes=METADATA,
                ),
            ) as reader:
                initialization = dict(reader.io_counters)
                with AnalysisReader(
                    reader,
                    artifact_ref=p["source"]["observations"],
                    limits=SavedViewLimits(
                        maximum_io_bytes=share, maximum_json_bytes=request["output_slot"] - 1024
                    ),
                ) as facade:
                    view = facade.brief()
            closed = dict(reader.io_counters)
            domain.check()
            item = dict(
                point_id=p["point_id"],
                view=view,
                access=dict(
                    status="checked_ro_current_request",
                    method="local_binding_mountinfo_nofollow_domain",
                    config_sha256=request["binding_sha256"],
                ),
            )
            _brief_binding(item, p, request["binding_sha256"])
            # Public return already respects the reserved slot; no partial page escapes.
            if len(_bytes(item)) > request["output_slot"]:
                fail("budget_exceeded")
            result["points"].append(item)
            result["credits"]["epochs"].append(
                dict(point_id=p["point_id"], initialization=initialization, brief_and_close=closed)
            )
            result["credits"]["actual_delivered_bytes"] += (
                initialization["bytes_read_total"] + closed["bytes_read_total"]
            )
            _bytes(result)
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
    return result


# Explicit retained-history operations. These credits cover one request, never
# scientific admission or host RSS. Numerical reductions belong only to Results.
HISTORY_PINS = ("native_result", "native_binding", "input_plan", "source_graph")
HISTORY_SELECTORS = ("run_uuid", "execution_id", "attempt", "calcjob_uuid")
HISTORY_COUNTS = ("observations", "updates", "finite_count", "nonfinite_count", "missing_count")
HISTORY_COPY = 128 * 1024 * 1024


def _history_schema(name, value):
    from qcl_negf_contracts import schema_validator

    validator = schema_validator("result-common.schema.json")
    try:
        validator.evolve(
            schema={"$ref": "#/$defs/" + name, "$defs": validator.schema["$defs"]}
        ).validate(value)
    except Exception as exc:
        raise SavedResultError("selection_required") from exc


def _history_selected(value):
    from qcl_negf_results.live_views import ScalarReduction

    keys = {
        *HISTORY_SELECTORS,
        "provider",
        "point_id",
        "retained",
        "source_state",
        "source_invocation",
        "reduction",
    }
    if type(value) is not dict or set(value) != keys or value["provider"] != "native_nfs":
        fail("selection_required")
    uuid(value["run_uuid"])
    uuid(value["calcjob_uuid"])
    for k in ("execution_id", "point_id"):
        if type(value[k]) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value[k]
        ):
            fail("selection_required")
    if type(value["attempt"]) is not int or not 1 <= value["attempt"] <= 9007199254740991:
        fail("selection_required")
    if type(value["retained"]) is not dict or set(value["retained"]) != set(HISTORY_PINS):
        fail("selection_required")
    for p in value["retained"].values():
        _node_pin(p)
    _history_schema("StateRef", value["source_state"])
    _history_schema("Invocation", value["source_invocation"])
    if (
        value["source_state"]["identity"]["execution_id"] != value["execution_id"]
        or value["source_state"]["identity"]["point_id"] != value["point_id"]
    ):
        fail("identity_mismatch")
    r = value["reduction"]
    if type(r) is not dict or set(r) != {"stage", "field", "first_record", "last_record"}:
        fail("selection_required")
    try:
        ScalarReduction(**r)
    except ValueError, TypeError:
        fail("selection_required")
    return value


def history_query(operation, selection, query):
    """Closed, finite metadata admission before actor dispatch or any child."""
    if len(canonical(query)) > 65536:
        fail("budget_exceeded")
    if operation == "derive-history":
        values, assessment = [_history_selected({**selection, **query})], None
        if set(query) != {
            "provider",
            "point_id",
            "retained",
            "source_state",
            "source_invocation",
            "reduction",
        }:
            fail("selection_required")
    elif operation == "compare-history":
        if type(query) is not dict or set(query) != {"left", "right", "comparability"}:
            fail("selection_required")
        values = [_history_selected(query[k]) for k in ("left", "right")]
        if not same({k: values[0][k] for k in HISTORY_SELECTORS}, selection):
            fail("identity_mismatch")
        assessment = query["comparability"]
        if (
            type(assessment) is not dict
            or set(assessment)
            != {"model", "discretization", "protocol", "source", "evidence", "reason"}
            or any(
                assessment[k] not in ("unknown", "different", "comparable")
                for k in ("model", "discretization", "protocol", "source")
            )
            or type(assessment["evidence"]) is not list
            or len(assessment["evidence"]) > 16
        ):
            fail("selection_required")
        for text in [assessment["reason"], *assessment["evidence"]]:
            if type(text) is not str or not 1 <= len(text.encode("utf-8")) <= 4096:
                fail("selection_required")
    else:
        fail("selection_required")
    return values, assessment


def _history_forecast(points):
    # Each source can return two catalog locators and one record-sized unit.
    # The exact pinned manifest byte size also bounds returned cutoff/status
    # strings and the history-head FileRef; those are not catalog locators.
    # x6 bounds JSON escaping before producing any detached native result;
    # three slots reserve native detach, child/parent copies and JSON encoding.
    # Source/request wrappers are exact admitted bytes; counts have UInt64 width.
    raw = sum(
        6
        * (
            2 * METADATA
            + METADATA
            + int(p["selected"]["source_state"]["manifest"]["bytes"])
            + len(canonical(p))
        )
        + 16384
        for p in points
    )
    reserved = 3 * raw + 65536
    if reserved > HISTORY_COPY:
        fail("budget_exceeded")
    return dict(
        raw_result_upper_bound=raw,
        copies_and_encoding=3,
        reserved_bytes=reserved,
        maximum_bytes=HISTORY_COPY,
        nonrefundable=True,
        basis="two_catalog_locators_plus_record_unit_and_exact_source_wrappers",
    )


def _history_perform(adapter, operation, selection, query, cancelled, deadline, check):
    values, assessment = history_query(operation, selection, query)
    method = getattr(adapter.service, "get_native_result_source", None)
    if not callable(method):
        fail("unsupported_contract")
    # Group first, so two points of one owner share its original whole DTO.
    groups = {}
    for index, v in enumerate(values):
        key = canonical(
            [{k: v[k] for k in HISTORY_SELECTORS}, v["retained"]["native_binding"]["node_uuid"]]
        )
        groups.setdefault(key, []).append((index, v))
    points = [None] * len(values)
    receipts = [None] * len(values)
    # Reserve both public owner ceilings before the first read. No getter exposes
    # an actual ledger, so these nonrefundable slots never become measured credit.
    metadata_reserved = _history_credits(len(values), len(groups), None)[
        "metadata_readback_reserved"
    ]
    local = adapter.binding
    if local is None:
        fail("unavailable_storage")
    for group in groups.values():
        v = group[0][1]
        owner = {k: v[k] for k in HISTORY_SELECTORS}
        check()
        dto = method(
            v["run_uuid"],
            v["execution_id"],
            attempt=v["attempt"],
            calcjob_uuid=v["calcjob_uuid"],
            point_id=None,
            binding_uuid=v["retained"]["native_binding"]["node_uuid"],
        )
        check()
        binding, pins, declared, selected = _catalog_dto(dto, owner)
        storage = binding["storage"]
        if (
            any(storage.get(k) != local[k] for k in REFS)
            or storage.get("computer_uuid") not in local["allowed_computer_uuids"]
        ):
            fail("unavailable_storage")
        for index, v in group:
            if not same(v["retained"], pins):
                fail("source_changed")
            if v["point_id"] not in selected:
                fail("capability_unavailable")
            p = selected[v["point_id"]][0]
            source = p["source"]
            if source["kind"] == "absent":
                fail("capability_unavailable")
            if not same(source["selected_state"], v["source_state"]) or not same(
                p["source_invocation"], v["source_invocation"]
            ):
                fail("source_changed")
            relative = path(storage["relative_execution_root"])
            if source["artifact_root"] != ".":
                relative = path(relative + "/" + path(source["artifact_root"]))
            # Detach only bounded selected records; never retain a whole previous owner.
            points[index] = _json(
                canonical(dict(selected=v, source=source, relative_root=relative))
            )
            receipts[index] = _json(
                canonical(
                    dict(
                        provider_selection={k: v[k] for k in HISTORY_SELECTORS},
                        point_id=v["point_id"],
                        point_attempt=str(p["point_attempt"]),
                        retained=dict(
                            **pins,
                            creator=p["creator"],
                            integrity=dict(
                                readback=dto["integrity"], original_binding=binding["integrity"]
                            ),
                        ),
                        source_state=v["source_state"],
                        source_invocation=v["source_invocation"],
                    )
                )
            )
        del dto, binding, pins, declared, selected, p, source, storage
    forecast = _history_forecast(points)
    request = dict(
        binding=local,
        binding_sha256=sha(canonical(local)),
        points=points,
        comparability=assessment,
        owner_count=len(groups),
        metadata_reserved=metadata_reserved,
        copy_workspace=forecast,
    )
    _bytes(request)
    spool = tempfile.TemporaryDirectory(prefix="qcl-native-history-")
    primary = None
    try:
        check()
        result = adapter.worker(operation, request, Path(spool.name), cancelled, deadline)
        check()
        _history_reply(
            result, points, operation, assessment, len(groups), forecast, request["binding_sha256"]
        )
        for r, actual in zip(receipts, result["sources"], strict=True):
            r.update(actual)
        if operation == "derive-history":
            r, v = receipts[0], result["view"]
            answer = dict(
                schema="qcl-negf.portal-selected-reader.v2",
                operation=operation,
                provider_selection=selection,
                provider="native_nfs",
                selection_mode="retained",
                source_state=r["source_state"],
                retained={
                    k: r["retained"][k]
                    for k in ("native_result", "native_binding", "creator", "integrity")
                },
                access=r["access"],
                view=dict(
                    result=v,
                    source_invocation=r["source_invocation"],
                    history_cutoff=r["history_cutoff"],
                    source_status=r["source_status"],
                    source_closed=r["source_closed"],
                    input_plan=r["retained"]["input_plan"],
                    source_graph=r["retained"]["source_graph"],
                    credits=result["credits"],
                    scientific_acceptance="not_assessed",
                ),
            )
        else:
            answer = dict(
                schema="qcl-negf.portal-native-history-comparison.v1",
                operation=operation,
                left=receipts[0],
                right=receipts[1],
                view=result["view"],
                assessment_origin="author_declared",
                credits=result["credits"],
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


def _history_credits(count, owners, forecast):
    share = PAGE_IO // (3 * count)
    return dict(
        source_io_total=PAGE_IO,
        epochs_per_source=3,
        source_count=count,
        stage_share=share,
        reserved_bytes=3 * count * share,
        actual_delivered_bytes=0,
        epochs=[],
        metadata_readback_reserved=dict(
            owners=owners,
            metadata_bytes=owners * 320 * 1024 * 1024,
            logical_io_bytes=owners * 256 * 1024 * 1024,
        ),
        metadata_readback_actual=None,
        metadata_readback_actual_reason="not_returned_by_public_owner_API",
        copy_workspace=forecast,
        scope="logical_source_metadata_copy_credit_not_RSS",
    )


def _history_read(request):
    from qcl_negf_results.live_views import ScalarReduction, compare_history, derive_history
    from qcl_negf_results.state import ResultReadLimits, StateReader

    if set(request) != {
        "operation",
        "binding",
        "binding_sha256",
        "points",
        "comparability",
        "owner_count",
        "metadata_reserved",
        "copy_workspace",
    }:
        fail("selection_required")
    operation, points = request["operation"], request["points"]
    if type(points) is not list or len(points) != (1 if operation == "derive-history" else 2):
        fail("selection_required")
    values = [_history_selected(p["selected"]) for p in points]
    selection = {k: values[0][k] for k in HISTORY_SELECTORS}
    query = (
        {k: v for k, v in values[0].items() if k not in HISTORY_SELECTORS}
        if operation == "derive-history"
        else dict(left=values[0], right=values[1], comparability=request["comparability"])
    )
    history_query(operation, selection, query)
    owners = len(
        {
            canonical(
                [{k: v[k] for k in HISTORY_SELECTORS}, v["retained"]["native_binding"]["node_uuid"]]
            )
            for v in values
        }
    )
    forecast = _history_forecast(points)
    if (
        type(request["owner_count"]) is not int
        or request["owner_count"] != owners
        or not same(forecast, request["copy_workspace"])
        or operation == "derive-history"
        and request["comparability"] is not None
    ):
        fail("identity_mismatch")
    binding = validate_binding(request["binding"])
    if sha(canonical(binding)) != request["binding_sha256"]:
        fail("identity_mismatch")
    credits = _history_credits(len(points), owners, forecast)
    if not same(credits["metadata_readback_reserved"], request["metadata_reserved"]):
        fail("identity_mismatch")
    share = credits["stage_share"]
    readers, domains, sources, epochs = [], [], [], []
    primary = None
    try:
        for index, (p, v) in enumerate(zip(points, values, strict=True)):
            if set(p) != {"selected", "source", "relative_root"} or not same(
                p["source"]["selected_state"], v["source_state"]
            ):
                fail("identity_mismatch")
            domain = _NativeDomain(binding, path(p["relative_root"]))
            domains.append(domain)
            reader = StateReader(
                domain.root,
                state_ref=v["source_state"],
                expected_identity=v["source_state"]["identity"],
                limits=ResultReadLimits(
                    maximum_metadata_bytes=METADATA,
                    maximum_io_bytes=share,
                    maximum_catalog_pages=4096,
                    maximum_catalog_entries=4096,
                    maximum_catalog_bytes=METADATA,
                    maximum_open_files=4,
                    maximum_batch_bytes=METADATA,
                    maximum_record_bytes=METADATA,
                    maximum_batch_records=256,
                    maximum_hdf_objects=4096,
                    maximum_array_bytes=METADATA,
                ),
            )
            readers.append(reader)
            epochs.append(
                dict(source=index, phase="construction", counters=dict(reader.io_counters))
            )
            description = reader.source
            if not same(description.state, v["source_state"]) or not same(
                description.invocation, v["source_invocation"]
            ):
                fail("source_changed")
            if int(v["reduction"]["last_record"]) > int(description.history["last_record"]):
                fail("selection_required")
            reader.import_history()
            epochs.append(dict(source=index, phase="import", counters=dict(reader.io_counters)))
            sources.append(
                dict(
                    history_cutoff=description.history,
                    source_status=description.status,
                    source_closed=description.closed,
                    access=dict(
                        status="checked_ro_current_request",
                        method="local_binding_mountinfo_nofollow_domain",
                        config_sha256=request["binding_sha256"],
                    ),
                )
            )
        reductions = [ScalarReduction(**v["reduction"]) for v in values]
        if operation == "derive-history":
            view = derive_history(readers[0], reductions[0], maximum_io_bytes=share)
        else:
            view = compare_history(
                readers[0],
                readers[1],
                left_request=reductions[0],
                right_request=reductions[1],
                comparability=request["comparability"],
                maximum_io_bytes=2 * share,
            )
        # Close every reader before domains; one failed close must not skip another.
        first = None
        for index, reader in enumerate(readers):
            try:
                reader.close()
            except BaseException as exc:
                first = first or exc
            epochs.append(
                dict(source=index, phase="derive_and_close", counters=dict(reader.io_counters))
            )
        readers.clear()
        if first is not None:
            raise first
        for domain in domains:
            domain.check()
        results = [view] if operation == "derive-history" else [view["left"], view["right"]]
        for result in results:
            for k in HISTORY_COUNTS:
                result[k] = str(result[k])
        credits["epochs"] = sorted(
            epochs,
            key=lambda e: (
                e["source"],
                ("construction", "import", "derive_and_close").index(e["phase"]),
            ),
        )
        credits["actual_delivered_bytes"] = sum(e["counters"]["bytes_read_total"] for e in epochs)
        result = dict(view=view, sources=sources, credits=credits)
        _history_reply(
            result,
            points,
            operation,
            request["comparability"],
            owners,
            forecast,
            request["binding_sha256"],
        )
        _bytes(result)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        errors = []
        for owner in [*reversed(readers), *reversed(domains)]:
            try:
                owner.close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            if primary is not None:
                primary.cleanup_unconfirmed = True
            else:
                raise errors[0]
    return result


def _history_reply(result, points, operation, assessment, owners, forecast, binding_sha):
    if type(result) is not dict or set(result) != {"view", "sources", "credits"}:
        fail()
    credits = result["credits"]
    expected = _history_credits(len(points), owners, forecast)
    if (
        type(credits) is not dict
        or set(credits) != set(expected)
        or any(
            not same(credits[k], v)
            for k, v in expected.items()
            if k not in ("epochs", "actual_delivered_bytes")
        )
        or type(credits["epochs"]) is not list
        or len(credits["epochs"]) != 3 * len(points)
    ):
        fail()
    measured = 0
    counter_keys = {
        "bytes_read_total",
        "sha_bytes",
        "hdf_bytes",
        "metadata_bytes",
        "logical_selected_bytes",
        "maximum_io_bytes",
    }
    for index, epoch in enumerate(credits["epochs"]):
        phase = ("construction", "import", "derive_and_close")[index % 3]
        if (
            type(epoch) is not dict
            or set(epoch) != {"source", "phase", "counters"}
            or epoch["source"] != index // 3
            or epoch["phase"] != phase
        ):
            fail()
        c = epoch["counters"]
        if (
            type(c) is not dict
            or set(c) != counter_keys
            or any(type(v) is not int or v < 0 for v in c.values())
            or c["maximum_io_bytes"] != credits["stage_share"]
            or c["bytes_read_total"] > credits["stage_share"]
            or c["bytes_read_total"] != c["sha_bytes"] + c["hdf_bytes"] + c["metadata_bytes"]
        ):
            fail()
        measured += c["bytes_read_total"]
    if (
        type(credits["actual_delivered_bytes"]) is not int
        or credits["actual_delivered_bytes"] != measured
    ):
        fail()
    view = result["view"]
    if operation == "compare-history":
        if (
            type(view) is not dict
            or set(view)
            != {"left", "right", "comparability", "differences", "scientific_acceptance"}
            or not same(view["comparability"], assessment)
            or view["scientific_acceptance"] != "not_assessed"
        ):
            fail("identity_mismatch")
        if any(
            assessment[k] != "comparable" for k in ("model", "discretization", "protocol", "source")
        ):
            if view["differences"] is not None:
                fail()
        if view["differences"] is not None:
            if type(view["differences"]) is not dict or set(view["differences"]) != {
                "minimum",
                "maximum",
            }:
                fail()
            for atom in view["differences"].values():
                if atom is not None:
                    _history_ieee(atom, difference=True)
        results = [view["left"], view["right"]]
    else:
        results = [view]
    if type(result["sources"]) is not list or len(result["sources"]) != len(points):
        fail()
    for p, actual, derived in zip(points, result["sources"], results, strict=True):
        v = p["selected"]
        if (
            type(actual) is not dict
            or set(actual) != {"history_cutoff", "source_status", "source_closed", "access"}
            or type(actual["source_closed"]) is not bool
            or actual["access"]
            != dict(
                status="checked_ro_current_request",
                method="local_binding_mountinfo_nofollow_domain",
                config_sha256=binding_sha,
            )
        ):
            fail()
        _history_schema("HistoryPin", actual["history_cutoff"])
        _history_schema("Status", actual["source_status"])
        if (
            type(derived) is not dict
            or set(derived)
            != {
                "source",
                "request",
                "units",
                *HISTORY_COUNTS,
                "minimum",
                "maximum",
                "coverage",
                "io_counters",
                "derived_by",
            }
            or not same(derived["source"], v["source_state"])
            or not same(derived["request"], v["reduction"])
            or derived["derived_by"] != "qcl_negf_results.live_views.derive_history.v1"
            or derived["units"] is not None
            and type(derived["units"]) is not str
            or not same(
                derived["coverage"],
                dict(
                    first_record=v["reduction"]["first_record"],
                    last_record=v["reduction"]["last_record"],
                    witness_updates="counted_separately_no_scalar_replacement",
                ),
            )
        ):
            fail("identity_mismatch")
        for k in HISTORY_COUNTS:
            if (
                type(derived[k]) is not str
                or not re.fullmatch(r"0|[1-9][0-9]{0,19}", derived[k])
                or int(derived[k]) > 2**64 - 1
            ):
                fail()
        for k in ("minimum", "maximum"):
            sample = derived[k]
            if sample is None:
                continue
            if type(sample) is not dict or set(sample) != {"value", "locator"}:
                fail()
            _history_ieee(sample["value"])
            locator = sample["locator"]
            _history_schema("RowLocator", locator)
            if (
                not same(locator["source_identity"], v["source_state"]["identity"])
                or locator["source_epoch"] != v["source_invocation"]["source_epoch"]
                or not int(v["reduction"]["first_record"])
                <= int(locator["append_ordinal"])
                <= int(v["reduction"]["last_record"])
            ):
                fail("identity_mismatch")


def _history_ieee(atom, *, difference=False):
    if (
        type(atom) is not dict
        or set(atom) != {"kind", "type", "bits"}
        or atom["kind"] != "ieee"
        or atom["type"] not in (("f64",) if difference else ("f32", "f64"))
        or type(atom["bits"]) is not str
        or not re.fullmatch(
            "[0-9a-f]{" + str(16 if atom["type"] == "f64" else 8) + "}", atom["bits"]
        )
    ):
        fail()
