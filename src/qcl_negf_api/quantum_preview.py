"""Trusted delivery/admission and finite quantum transport; no scientific resolver."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from pathlib import Path

from .configurator import ConfiguratorError, _json

RECORD = "/var/lib/qcl-negf-admin/runtime/release.json"
KEYS = {
    "release_id",
    "solver_executable",
    "solver_output",
    "nar_hash",
    "drv_path",
    "source_graph_sha256",
    "julia_manifest_sha256",
    "runner_source_id",
    "core_source_id",
    "context_sha256",
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
REV = re.compile(r"[0-9a-f]{40}\Z")
STORE = re.compile(r"/nix/store/[a-z0-9]{32}-[A-Za-z0-9+._?=-]+\Z")
REQUIRED_MEMORY = 2684354560


class QuantumRefusal(ConfiguratorError):
    def __init__(self, value, request):
        status = value.get("status")
        reason = value.get("reason")
        if (
            status not in ("error", "unavailable")
            or not isinstance(reason, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason)
            or value.get("plan_changed") is not False
        ):
            raise ConfiguratorError("invalid_response")
        super().__init__("unavailable" if status == "unavailable" else "runner_refused")

        def number(v):
            return (
                v if type(v) in (int, float) and abs(v) <= 2**63 - 1 and math.isfinite(v) else None
            )

        self.response = {
            "schema": "qcl-negf.quantum-preview.v1",
            "status": status,
            "reason": reason,
            "actual": number(value.get("actual")),
            "allowed": number(value.get("allowed")),
            "plan_changed": False,
            "identity": {
                k: request[k] for k in ("plan_sha256", "execution_id", "point_id", "variant_id")
            },
        }
        plan = _json(request["plan"].encode())
        for key in ("fingerprint", "scientific_fingerprint"):
            value = plan.get(key)
            self.response["identity"][key] = (
                value if isinstance(value, str) and SHA.fullmatch(value) else None
            )


def refuse():
    raise ConfiguratorError("unavailable")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def validate_context(value):
    if (
        not isinstance(value, dict)
        or set(value) != KEYS
        or any(
            not isinstance(v, str)
            or not 1 <= len(v) <= 512
            or not v.isascii()
            or any(ord(c) < 32 for c in v)
            for v in value.values()
        )
        or any(
            not SHA.fullmatch(value[k])
            for k in ("source_graph_sha256", "julia_manifest_sha256", "context_sha256")
        )
        or any(not REV.fullmatch(value[k]) for k in ("runner_source_id", "core_source_id"))
        or not SHA.fullmatch(value["release_id"])
        or not STORE.fullmatch(value["solver_output"])
        or not STORE.fullmatch(value["drv_path"])
        or not value["drv_path"].endswith(".drv")
        or value["solver_executable"] != value["solver_output"] + "/bin/qcl-negf"
        or not re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", value["nar_hash"])
    ):
        refuse()
    expected = hashlib.sha256(
        canonical({k: v for k, v in value.items() if k != "context_sha256"})
    ).hexdigest()
    if expected != value["context_sha256"]:
        refuse()
    return value


def protected_record(path=RECORD):
    if path != RECORD:
        refuse()
    directories = [Path("/"), *reversed(Path(path).parent.parents), Path(path).parent]
    for directory in directories:
        value = directory.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022:
            refuse()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_mode & 0o022
            or before.st_size > 65536
        ):
            refuse()
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
        if len(raw) > 65536 or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            refuse()
    return _json(raw)


def _profile_targets():
    profile = Path("/nix/var/nix/profiles/qcl-negf-application")
    return str(profile.resolve(strict=True)), str(
        Path(str(profile) + "-solver").resolve(strict=True)
    )


def runtime_context(
    settings, *, environment=None, read_record=protected_record, profile_targets=_profile_targets
):
    environment = os.environ if environment is None else environment
    try:
        if environment.get("QCL_NEGF_QUANTUM_RUNTIME_RELEASE_FILE") != RECORD:
            refuse()
        record = read_record()
        if not isinstance(record, dict):
            refuse()
        context = validate_context(record.get("quantum_runtime_context"))
        if (
            record.get("ready") is not True
            or context["solver_executable"] != settings.configurator_executable
            or context["solver_executable"] != record.get("solver_executable")
            or context["solver_executable"] != environment.get("QCL_NEGF_SOLVER_EXECUTABLE")
            or context["release_id"] != record.get("release_id")
            or context["release_id"] != environment.get("QCL_NEGF_RELEASE_ID")
            or context["context_sha256"]
            != environment.get("QCL_NEGF_QUANTUM_RUNTIME_CONTEXT_SHA256")
        ):
            refuse()
        application_target, solver_target = profile_targets()
        if (
            application_target != record.get("application_path")
            or solver_target != context["solver_output"]
        ):
            refuse()
        graph = record.get("sources")
        build = record.get("solver_build")
        closures = record.get("closures")
        if (
            not isinstance(graph, dict)
            or set(graph) != {"format", "revision", "components"}
            or graph["format"] != "qcl-negf.git-graph.v1"
            or not isinstance(graph["revision"], str)
            or not REV.fullmatch(graph["revision"])
            or not isinstance(graph["components"], list)
            or not 1 <= len(graph["components"]) <= 64
        ):
            refuse()
        refs = {}
        for row in graph["components"]:
            if (
                not isinstance(row, dict)
                or set(row) != {"path", "revision", "url"}
                or row.get("path") in refs
            ):
                refuse()
            for key, pattern in (
                ("path", r"components/[A-Za-z0-9][A-Za-z0-9.-]*\Z"),
                ("revision", r"[0-9a-f]{40}\Z"),
                ("url", r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+\Z"),
            ):
                if (
                    not isinstance(row[key], str)
                    or len(row[key]) > 256
                    or not re.fullmatch(pattern, row[key])
                ):
                    refuse()
            refs[row["path"]] = row["revision"]
        if (
            refs.get("components/QCLNEGF.jl") != context["core_source_id"]
            or refs.get("components/QCLNEGFRunner.jl") != context["runner_source_id"]
            or hashlib.sha256(canonical(graph)).hexdigest() != context["source_graph_sha256"]
        ):
            refuse()
        if not isinstance(build, dict) or set(build) != {
            "source_graph_sha256",
            "julia_manifest_sha256",
            "drv_path",
            "output_path",
        }:
            refuse()
        if any(
            build[k] != context[c]
            for k, c in (
                ("source_graph_sha256", "source_graph_sha256"),
                ("julia_manifest_sha256", "julia_manifest_sha256"),
                ("drv_path", "drv_path"),
                ("output_path", "solver_output"),
            )
        ):
            refuse()
        if not isinstance(closures, list) or not 1 <= len(closures) <= 64:
            refuse()
        owners = [
            r for r in closures if isinstance(r, dict) and r.get("path") == context["solver_output"]
        ]
        if len(owners) != 1 or owners[0].get("narHash") != context["nar_hash"]:
            refuse()
        return context
    except (OSError, ValueError, KeyError, TypeError, ConfiguratorError) as error:
        raise ConfiguratorError("unavailable") from error


def decode_quantum_request(raw, context):
    if len(raw) > 1536 * 1024:
        raise ConfiguratorError("limit")
    value = _json(raw)
    keys = {
        "schema",
        "plan",
        "plan_sha256",
        "expected_runner_source_id",
        "expected_core_source_id",
        "execution_id",
        "point_id",
        "variant_id",
        "budget_policy_id",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or value["schema"] != "qcl-negf.quantum-preview-request.v1"
        or value["budget_policy_id"] != "quantum-preview-reference-v1"
        or value["expected_runner_source_id"] != context["runner_source_id"]
        or value["expected_core_source_id"] != context["core_source_id"]
        or not isinstance(value["plan"], str)
        or len(value["plan"].encode()) > 1024 * 1024
        or hashlib.sha256(value["plan"].encode()).hexdigest() != value["plan_sha256"]
        or any(
            not isinstance(value[k], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}", value[k])
            for k in ("point_id", "execution_id", "variant_id")
        )
    ):
        raise ConfiguratorError("invalid_envelope")
    plan = _json(value["plan"].encode())
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("executions"), list)
        or len(plan["executions"]) > 64
        or not isinstance(plan.get("points"), list)
        or len(plan["points"]) > 256
        or any(not isinstance(row, dict) for row in [*plan["executions"], *plan["points"]])
    ):
        raise ConfiguratorError("invalid_envelope")
    executions = [e for e in plan["executions"] if e.get("id") == value["execution_id"]]
    points = [
        p
        for p in plan["points"]
        if p.get("id") == value["point_id"] and p.get("execution_id") == value["execution_id"]
    ]
    if (
        len(executions) != 1
        or len(points) != 1
        or executions[0].get("variant_id") != value["variant_id"]
    ):
        raise ConfiguratorError("invalid_envelope")
    return value


def decode_quantum_response(raw, request, context):
    if len(raw) > 2 * 1024 * 1024:
        raise ConfiguratorError("limit")
    value = _json(raw)
    if not isinstance(value, dict) or value.get("schema") != "qcl-negf.quantum-preview.v1":
        raise ConfiguratorError("invalid_response")
    if value.get("status") != "available":
        raise QuantumRefusal(value, request)
    identity = value.get("identity")
    payload = value.get("payload")
    if (
        not isinstance(identity, dict)
        or not isinstance(payload, dict)
        or any(
            identity.get(k) != request[k]
            for k in ("plan_sha256", "execution_id", "point_id", "variant_id")
        )
        or identity.get("runner_source_id") != context["runner_source_id"]
        or identity.get("core_source_id") != context["core_source_id"]
    ):
        raise ConfiguratorError("invalid_response")
    runtime = identity.get("runtime")
    frozen = _json(request["plan"].encode())
    point = next(
        (
            p
            for p in frozen["points"]
            if p.get("id") == request["point_id"]
            and p.get("execution_id") == request["execution_id"]
        ),
        None,
    )
    if (
        identity.get("fingerprint") != frozen.get("fingerprint")
        or identity.get("scientific_fingerprint") != frozen.get("scientific_fingerprint")
        or not isinstance(point, dict)
        or not isinstance(point.get("effective_inputs"), dict)
        or identity.get("point_passport") != point["effective_inputs"]
        or any(
            not isinstance(identity.get(k), str) or not SHA.fullmatch(identity[k])
            for k in ("fingerprint", "scientific_fingerprint")
        )
    ):
        raise ConfiguratorError("invalid_response")
    dimensions = payload.get("dimensions")
    shape = payload.get("shape")
    if (
        not isinstance(runtime, dict)
        or runtime.get("context_sha256") != context["context_sha256"]
        or runtime.get("runner_source_id") != context["runner_source_id"]
        or runtime.get("core_source_id") != context["core_source_id"]
        or not isinstance(dimensions, dict)
        or not isinstance(shape, list)
        or len(shape) != 2
        or any(type(x) is not int or x < 1 for x in shape)
        or shape != [dimensions.get("Nz"), dimensions.get("Nb")]
        or shape[1] > 32
        or shape[0] * shape[1] > 16384
        or value.get("budget_policy_id") != "quantum-preview-reference-v1"
        or value.get("policy_revision") != 1
        or value.get("payload_digest_encoding") != "qcl-negf-canonical-bytes-v1"
        or any(
            not isinstance(value.get(k), str) or not SHA.fullmatch(value[k])
            for k in ("preview_id", "payload_sha256")
        )
        or value.get("assessment")
        != {"SCBA": "not_performed", "Poisson": "not_performed", "scientific": "not_assessed"}
    ):
        raise ConfiguratorError("invalid_response")

    def vector(v, n):
        return (
            isinstance(v, list)
            and len(v) == n
            and all(
                type(x) in (int, float)
                and (type(x) is not int or abs(x) <= 9007199254740991)
                and math.isfinite(x)
                for x in v
            )
        )

    nz, nb = shape
    if (
        not vector(payload.get("energy_eV"), nb)
        or not vector(payload.get("z_nm"), nz)
        or not vector(payload.get("band_edge_eV"), nz)
        or any(
            not isinstance(payload.get(k), list)
            or len(payload[k]) != nz
            or any(not vector(row, nb) for row in payload[k])
            for k in ("psi_real", "psi_imag", "density_per_nm")
        )
        or payload.get("units") != {"energy": "eV", "z": "nm", "psi": "nm^-1/2", "density": "nm^-1"}
    ):
        raise ConfiguratorError("invalid_response")
    semantics = payload.get("semantics")
    if not isinstance(semantics, dict) or any(
        semantics.get(k) != v
        for k, v in {
            "scope": "bare_onsite_k0",
            "hartree": "zero",
            "kappa": 0,
            "interperiod_hopping": "excluded",
            "window_boundary": "Dirichlet",
        }.items()
    ):
        raise ConfiguratorError("invalid_response")


def _small(path, maximum=65536):
    with Path(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        refuse()
    return raw.decode("ascii")


def memory_admission(cgroup_root):
    """Fresh guest and all visible unified ancestors. Unknown mandatory facts hold."""
    began = time.monotonic()
    try:
        boot = _small("/proc/sys/kernel/random/boot_id", 128).strip()
        uuid_pattern = (
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        if not re.fullmatch(uuid_pattern, boot):
            refuse()
        rows = {}
        for line in _small("/proc/meminfo").splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                fields = rest.split()
                if len(fields) != 2 or not fields[0].isdigit() or fields[1] != "kB" or key in rows:
                    refuse()
                rows[key] = int(fields[0]) * 1024
        if not REQUIRED_MEMORY <= rows["MemAvailable"] <= rows[
            "MemTotal"
        ] or not os.sched_getaffinity(0):
            refuse()
        root = Path(cgroup_root)
        mount = None
        mount_root = None
        mountinfo = _small("/proc/self/mountinfo", 256 * 1024)
        membership = _small("/proc/self/cgroup")
        namespace = os.readlink("/proc/self/ns/cgroup")
        for line in mountinfo.splitlines():
            before, _, after = line.partition(" - ")
            fields = before.split()
            if (
                after.split()[0] == "cgroup2"
                and "\\" not in fields[4]
                and root.is_relative_to(fields[4])
            ):
                if mount is not None:
                    refuse()
                mount = Path(fields[4])
                mount_root = fields[3]
        if mount is None:
            refuse()
        # Deployment prerequisite: the approved existing API unit denies new
        # namespaces (RestrictNamespaces=true), and native DEPLOYBIND confirms
        # its initial guest cgroup namespace/full hierarchy. Layout alone is
        # not a universal proof. Unprivileged requests inspect self only.
        global_root_layout = mount_root == "/"
        if not global_root_layout:
            refuse()
        entries = membership.splitlines()
        if (
            len(entries) != 1
            or not entries[0].startswith("0::/")
            or any(part in (".", "..") for part in entries[0][3:].split("/"))
            or mount / entries[0][3:].lstrip("/") != root / "api"
        ):
            refuse()

        def control(path, *, global_root=False):
            try:
                return _small(path, 65536).strip()
            except FileNotFoundError:
                if global_root:
                    return None
                raise

        event_observations = {}
        event_unknown_reasons = {}
        pressure_observations = {}
        while True:
            global_root = root == mount and global_root_layout
            current = control(root / "memory.current", global_root=global_root)
            if current is not None and not re.fullmatch(r"[0-9]+", current):
                refuse()
            current = int(current) if current is not None else None
            for filename in ("memory.max", "memory.high"):
                limit = control(root / filename, global_root=global_root)
                if (
                    limit is not None
                    and limit != "max"
                    and (
                        not re.fullmatch(r"[0-9]+", limit)
                        or current is None
                        or int(limit) > 2**63 - 1
                        or int(limit) - current < REQUIRED_MEMORY
                    )
                ):
                    refuse()
            swap = control(root / "memory.swap.current", global_root=global_root)
            if swap is not None and swap != "0":
                refuse()
            events = None
            event_reason = None
            try:
                event_raw = _small(root / "memory.events", 65536).strip()
                pairs = [row.split() for row in event_raw.splitlines()]
                if any(
                    len(row) != 2
                    or not re.fullmatch(r"[a-z_]+", row[0])
                    or not re.fullmatch(r"[0-9]+", row[1])
                    for row in pairs
                ) or len({row[0] for row in pairs}) != len(pairs):
                    raise ValueError("malformed historical counters")
                events = {key: int(value) for key, value in pairs}
                if not {"high", "max", "oom", "oom_kill"} <= events.keys() or any(
                    value > 2**64 - 1 for value in events.values()
                ):
                    raise ValueError("malformed historical counters")
            except OSError:
                event_reason = "unreadable"
            except (ValueError, ConfiguratorError):
                events = None
                event_reason = "malformed"
            # Historical totals are observations, never a permanent fresh veto.
            event_observations[str(root)] = events
            event_unknown_reasons[str(root)] = event_reason
            pressure = control(root / "memory.pressure", global_root=global_root)
            if pressure is None:
                pressure = _small("/proc/pressure/memory")
            pressure_observations[str(root)] = {}
            for kind in ("some", "full"):
                samples = [r for r in pressure.splitlines() if r.startswith(kind + " ")]
                if len(samples) != 1:
                    refuse()
                fields = {}
                for token in samples[0].split()[1:]:
                    key, _, value = token.partition("=")
                    fields.setdefault(key, []).append(value)
                if len(fields.get("avg10", [])) != 1:
                    refuse()
                avg10 = float(fields["avg10"][0])
                if not math.isfinite(avg10) or avg10 != 0:
                    refuse()
                rates = {"avg10": avg10}
                unknown_reasons = {}
                for key in ("avg60", "avg300"):
                    try:
                        values = fields.get(key, [])
                        if len(values) != 1:
                            raise ValueError("missing or duplicate historical average")
                        value = float(values[0])
                        if not math.isfinite(value) or value < 0:
                            raise ValueError("malformed historical average")
                        rates[key] = value
                    except ValueError:
                        rates[key] = None
                        unknown_reasons[key] = "missing_or_malformed"
                rates["unknown_reasons"] = unknown_reasons
                pressure_observations[str(root)][kind] = rates
            quota_raw = control(root / "cpu.max", global_root=global_root)
            quota = quota_raw.split() if quota_raw is not None else None
            if quota is not None and (
                len(quota) != 2
                or not quota[1].isdigit()
                or int(quota[1]) < 1
                or (quota[0] != "max" and (not quota[0].isdigit() or int(quota[0]) < int(quota[1])))
            ):
                refuse()
            if root == mount:
                break
            if root.parent == root:
                refuse()
            root = root.parent
        if (
            _small("/proc/sys/kernel/random/boot_id", 128).strip() != boot
            or _small("/proc/self/cgroup") != membership
            or _small("/proc/self/mountinfo", 256 * 1024) != mountinfo
            or os.readlink("/proc/self/ns/cgroup") != namespace
            or time.monotonic() - began > 5
        ):
            refuse()
        return {
            "observed_monotonic": began,
            "boot_id": boot,
            "membership": membership,
            "namespace": namespace,
            "mem_available_bytes": rows["MemAvailable"],
            "memory_events": event_observations,
            "memory_events_unknown_reasons": event_unknown_reasons,
            "psi": pressure_observations,
        }
    except (OSError, ValueError, KeyError, IndexError) as error:
        raise ConfiguratorError("unavailable") from error
