"""Source-bound fake-process contracts; no native quantum solve."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest
from test_configurator import Group, Process, settings

from qcl_negf_api.configurator import Configurator, ConfiguratorError
from qcl_negf_api.quantum_preview import decode_quantum_request, validate_context

PLAN = (
    '{ "fingerprint":"'
    + "a" * 64
    + '", "points":[{"id":"p","execution_id":"e"}], "executions":[{"id":"e","variant_id":"v"}] }\n'
)
CONTEXT = {
    "release_id": "a" * 64,
    "solver_executable": "/nix/store/" + "b" * 32 + "-solver/bin/qcl-negf",
    "solver_output": "/nix/store/" + "b" * 32 + "-solver",
    "nar_hash": "sha256-" + "A" * 43 + "=",
    "drv_path": "/nix/store/" + "b" * 32 + "-solver.drv",
    "source_graph_sha256": "c" * 64,
    "julia_manifest_sha256": "d" * 64,
    "runner_source_id": "e" * 40,
    "core_source_id": "f" * 40,
}
CONTEXT["context_sha256"] = hashlib.sha256(
    json.dumps(CONTEXT, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
REQUEST = {
    "schema": "qcl-negf.quantum-preview-request.v1",
    "plan": PLAN,
    "plan_sha256": hashlib.sha256(PLAN.encode()).hexdigest(),
    "expected_runner_source_id": "e" * 40,
    "expected_core_source_id": "f" * 40,
    "execution_id": "e",
    "point_id": "p",
    "variant_id": "v",
    "budget_policy_id": "quantum-preview-reference-v1",
}


def test_exact_raw_plan_unique_point_and_sources_without_resolution():
    raw = json.dumps(REQUEST).encode()
    assert decode_quantum_request(raw, CONTEXT)["plan"] == PLAN
    for patch in (
        {"plan_sha256": "0" * 64},
        {"point_id": "missing"},
        {"expected_core_source_id": "0" * 40},
        {"variant_id": "other"},
        {"env": {}},
    ):
        with pytest.raises(ConfiguratorError):
            decode_quantum_request(json.dumps({**REQUEST, **patch}).encode(), CONTEXT)
    data = json.loads(PLAN)
    data["points"].append(data["points"][0])
    plan = json.dumps(data)
    with pytest.raises(ConfiguratorError):
        decode_quantum_request(
            json.dumps(
                {**REQUEST, "plan": plan, "plan_sha256": hashlib.sha256(plan.encode()).hexdigest()}
            ).encode(),
            CONTEXT,
        )
    with pytest.raises(ConfiguratorError):
        validate_context({**CONTEXT, "context_sha256": "0" * 64})


def test_quantum_reuses_single_gate_and_raw_stream_with_fixed_context():
    async def case():
        group = Group()
        calls = []
        # Explicit transport DTO validator seam for lifecycle control only.
        expected = (
            json.dumps(
                {
                    "tiny": "raw",
                    "identity": {
                        "request_sha256": hashlib.sha256(json.dumps(REQUEST).encode()).hexdigest()
                    },
                }
            ).encode()
            + b"\n"
        )
        released = asyncio.Event()
        process = Process(expected, wait=released)

        async def spawn(*args, **kwargs):
            calls.append((args, kwargs))
            Path(args[-1]).write_bytes(expected)

            async def release():
                await asyncio.sleep(0.02)
                released.set()

            asyncio.create_task(release())
            return process

        runtime = Configurator(
            settings(),
            group=group,
            spawn=spawn,
            killpg=lambda _: None,
            quantum_binding=lambda: CONTEXT,
            memory_admission=lambda: None,
            quantum_decoder=lambda raw, request, context: None,
            wall_seconds=0.001,
        )
        result = await runtime.quantum(
            json.dumps(REQUEST).encode(), deadline=time.monotonic() + 180
        )
        assert result == expected and process.stdin.raw == json.dumps(REQUEST).encode()
        assert "quantum-preview" in calls[0][0]
        assert (
            calls[0][1]["env"]["QCL_NEGF_QUANTUM_RUNTIME_CONTEXT_SHA256"]
            == CONTEXT["context_sha256"]
        )
        assert group.events[-1] == "remove" and not runtime.busy
        runtime.busy = True
        with pytest.raises(ConfiguratorError, match="busy"):
            await runtime.quantum(json.dumps(REQUEST).encode())
        with pytest.raises(ConfiguratorError, match="busy"):
            await runtime.catalog()

    asyncio.run(case())


def test_memory_refusal_before_spawn_and_cancel_reaps_same_gate():
    async def case():
        calls = []

        async def spawn(*a, **k):
            calls.append(a)
            return Process()

        def refuse():
            raise ConfiguratorError("unavailable")

        runtime = Configurator(
            settings(),
            group=Group(),
            spawn=spawn,
            killpg=lambda _: None,
            quantum_binding=lambda: CONTEXT,
            memory_admission=refuse,
        )
        with pytest.raises(ConfiguratorError):
            await runtime.quantum(json.dumps(REQUEST).encode())
        assert calls == [] and not runtime.busy
        released = asyncio.Event()
        process = Process(wait=released)

        async def blocked(*a, **k):
            return process

        group = Group()
        group.kill = lambda leaf: released.set()
        runtime = Configurator(
            settings(),
            group=group,
            spawn=blocked,
            killpg=lambda _: None,
            quantum_binding=lambda: CONTEXT,
            memory_admission=lambda: None,
        )
        task = asyncio.create_task(runtime.quantum(json.dumps(REQUEST).encode()))
        while not runtime.busy:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not runtime.busy and process.waited

    asyncio.run(case())


def test_literal_fresh_memory_ancestors_and_proven_global_root(monkeypatch):
    from qcl_negf_api import quantum_preview as module

    pressure = (
        "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )
    files = {
        "/proc/sys/kernel/random/boot_id": "a" * 8
        + "-"
        + "b" * 4
        + "-"
        + "c" * 4
        + "-"
        + "d" * 4
        + "-"
        + "e" * 12,
        "/proc/self/cgroup": "0::/unit/api\n",
        "/proc/meminfo": "MemTotal: 4194304 kB\nMemAvailable: 3145728 kB\n",
        "/proc/self/mountinfo": "1 0 0:1 / /cg rw - cgroup2 cgroup rw\n",
        "/proc/pressure/memory": pressure,
    }
    for root in ("/cg/unit", "/cg"):
        for key, value in {
            "memory.current": "536870912",
            "memory.max": "4294967296",
            "memory.high": "max",
            "memory.swap.current": "0",
            "memory.events": "high 0\nmax 0\noom 0\noom_kill 0\n",
            "memory.pressure": pressure,
            "cpu.max": "max 100000",
        }.items():
            files[root + "/" + key] = value

    def read(path, maximum=65536):
        name = str(path)
        if name not in files:
            raise FileNotFoundError(name)
        if isinstance(files[name], (OSError, ConfiguratorError)):
            raise files[name]
        return files[name]

    monkeypatch.setattr(module, "_small", read)
    monkeypatch.setattr(module.os, "sched_getaffinity", lambda _: {0, 1})
    namespace_reads = []

    def self_only(path):
        namespace_reads.append(path)
        if path == "/proc/1/ns/cgroup":
            raise PermissionError("ptrace denied")
        assert path == "/proc/self/ns/cgroup"
        return "cgroup:[1]"

    monkeypatch.setattr(module.os, "readlink", self_only)
    # Approved API unit / native DEPLOYBIND is an external precondition; this
    # request fixture must not need privilege to inspect root PID1.
    module.memory_admission("/cg/unit")
    files["/cg/unit/memory.events"] = "high 17\nmax 9\noom 1\noom_kill 1\n"
    files["/cg/unit/memory.pressure"] = pressure.replace("avg60=0.00", "avg60=1.25").replace(
        "avg300=0.00", "avg300=2.50"
    )
    snapshot = module.memory_admission("/cg/unit")
    assert snapshot["memory_events"]["/cg/unit"] == {"high": 17, "max": 9, "oom": 1, "oom_kill": 1}
    assert snapshot["psi"]["/cg/unit"]["some"]["avg60"] == 1.25
    assert set(namespace_reads) == {"/proc/self/ns/cgroup"}
    old_events = files["/cg/unit/memory.events"]
    old_pressure = files["/cg/unit/memory.pressure"]
    for broken in (
        "high nan\nmax 9\noom 1\noom_kill 1\n",
        "high 1\nmax 9\noom 1\n",
        "high 1\nhigh 2\nmax 9\noom 1\noom_kill 1\n",
        f"high {2**64}\nmax 9\noom 1\noom_kill 1\n",
    ):
        files["/cg/unit/memory.events"] = broken
        snapshot = module.memory_admission("/cg/unit")
        assert snapshot["memory_events"]["/cg/unit"] is None
        assert snapshot["memory_events_unknown_reasons"]["/cg/unit"] == "malformed"
    for broken in (FileNotFoundError("missing"), PermissionError("unreadable")):
        files["/cg/unit/memory.events"] = broken
        snapshot = module.memory_admission("/cg/unit")
        assert snapshot["memory_events"]["/cg/unit"] is None
        assert snapshot["memory_events_unknown_reasons"]["/cg/unit"] == "unreadable"
    files["/cg/unit/memory.events"] = ConfiguratorError("unavailable")  # Bounded read overflow.
    snapshot = module.memory_admission("/cg/unit")
    assert snapshot["memory_events"]["/cg/unit"] is None
    assert snapshot["memory_events_unknown_reasons"]["/cg/unit"] == "malformed"
    files["/cg/unit/memory.events"] = old_events
    for key, original in (("avg60", "1.25"), ("avg300", "2.50")):
        for replacement in (f"{key}=nan", f"{key}=bad", "", f"{key}=1 {key}=2"):
            files["/cg/unit/memory.pressure"] = old_pressure.replace(
                f"{key}={original}", replacement
            )
            snapshot = module.memory_admission("/cg/unit")
            for kind in ("some", "full"):
                assert snapshot["psi"]["/cg/unit"][kind][key] is None
                assert key in snapshot["psi"]["/cg/unit"][kind]["unknown_reasons"]
                assert snapshot["psi"]["/cg/unit"][kind]["avg10"] == 0
    for replacement in ("avg10=0.01", "avg10=nan", "avg10=bad", "", "avg10=0 avg10=0"):
        files["/cg/unit/memory.pressure"] = old_pressure.replace("avg10=0.00", replacement)
        with pytest.raises(ConfiguratorError):
            module.memory_admission("/cg/unit")
    files["/cg/unit/memory.pressure"] = old_pressure
    for key in (
        "memory.max",
        "memory.high",
        "cpu.max",
        "memory.current",
        "memory.swap.current",
        "memory.events",
        "memory.pressure",
    ):
        files.pop("/cg/" + key)
    # Only demonstrated global root has no parent controls. Guest facts/PSI remain measured.
    module.memory_admission("/cg/unit")
    files.pop("/cg/unit/memory.max")
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")
    files["/cg/unit/memory.max"] = "2684354560"
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")
    files["/cg/unit/memory.max"] = "max"
    files["/proc/meminfo"] = "MemTotal: 4194304 kB\nMemAvailable: 2621439 kB\n"
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")


def test_delivery_record_exact_graph_context_and_profile_not_echo_labels():
    from types import SimpleNamespace

    from qcl_negf_api.quantum_preview import canonical, runtime_context

    graph = {
        "format": "qcl-negf.git-graph.v1",
        "revision": "a" * 40,
        "components": [
            {
                "path": "components/QCLNEGF.jl",
                "revision": "f" * 40,
                "url": "https://github.com/Afonenko-QCL-NEGF/QCLNEGF.jl",
            },
            {
                "path": "components/QCLNEGFRunner.jl",
                "revision": "e" * 40,
                "url": "https://github.com/Afonenko-QCL-NEGF/QCLNEGFRunner.jl",
            },
        ],
    }
    context = {**CONTEXT, "source_graph_sha256": hashlib.sha256(canonical(graph)).hexdigest()}
    context["context_sha256"] = hashlib.sha256(
        canonical({k: v for k, v in context.items() if k != "context_sha256"})
    ).hexdigest()
    record = {
        "ready": True,
        "release_id": context["release_id"],
        "solver_executable": context["solver_executable"],
        "application_path": "/nix/store/" + "1" * 32 + "-app",
        "quantum_runtime_context": context,
        "sources": graph,
        "solver_build": {
            k: context[c]
            for k, c in (
                ("source_graph_sha256", "source_graph_sha256"),
                ("julia_manifest_sha256", "julia_manifest_sha256"),
                ("drv_path", "drv_path"),
                ("output_path", "solver_output"),
            )
        },
        "closures": [{"path": context["solver_output"], "narHash": context["nar_hash"]}],
    }
    env = {
        "QCL_NEGF_QUANTUM_RUNTIME_RELEASE_FILE": "/var/lib/qcl-negf-admin/runtime/release.json",
        "QCL_NEGF_QUANTUM_RUNTIME_CONTEXT_SHA256": context["context_sha256"],
        "QCL_NEGF_RELEASE_ID": context["release_id"],
        "QCL_NEGF_SOLVER_EXECUTABLE": context["solver_executable"],
    }
    cfg = SimpleNamespace(configurator_executable=context["solver_executable"])

    def profiles():
        return record["application_path"], context["solver_output"]

    assert (
        runtime_context(cfg, environment=env, read_record=lambda: record, profile_targets=profiles)
        == context
    )
    for changed in (
        {"QCL_NEGF_RELEASE_ID": "foreign"},
        {"QCL_NEGF_QUANTUM_RUNTIME_CONTEXT_SHA256": "0" * 64},
        {"QCL_NEGF_QUANTUM_RUNTIME_RELEASE_FILE": "/tmp/forged.json"},
    ):
        with pytest.raises(ConfiguratorError):
            runtime_context(
                cfg,
                environment={**env, **changed},
                read_record=lambda: record,
                profile_targets=profiles,
            )
    with pytest.raises(ConfiguratorError):
        runtime_context(
            cfg,
            environment=env,
            read_record=lambda: {**record, "ready": False},
            profile_targets=profiles,
        )
    with pytest.raises(ConfiguratorError):
        runtime_context(
            cfg,
            environment=env,
            read_record=lambda: record,
            profile_targets=lambda: ("/wrong", context["solver_output"]),
        )


def test_quantum_http_auth_no_store_exact_raw_and_body_absolute_deadline(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from test_configurator_api import AUTH, CODE, TOKEN, Adapter

    from qcl_negf_api.api import BodyLimit, create_app
    from qcl_negf_api.settings import Settings

    class QuantumAdapter(Adapter):
        async def quantum(self, raw, disconnect=None, *, deadline=None):
            self.calls.append((raw, deadline))
            return b'{ "exact": 1.0000000000000001 }\n'

    adapter = QuantumAdapter()
    with TestClient(
        create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), configurator=adapter)
    ) as client:
        assert client.post("/api/v1/configurator/quantum-preview", content=b"{}").status_code == 401
        assert adapter.calls == []
        response = client.post(
            "/api/v1/configurator/quantum-preview", headers=AUTH, content=b'{ "raw": 1 }'
        )
        assert response.content == b'{ "exact": 1.0000000000000001 }\n'
        assert (
            response.headers["cache-control"] == "no-store"
            and adapter.calls[-1][0] == b'{ "raw": 1 }'
        )

    async def case():
        sent = []
        called = []

        async def application(*a):
            called.append(True)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": True}

        async def send(message):
            sent.append(message)

        async def timeout(awaitable, seconds):
            awaitable.close()
            assert 180 < seconds <= 185
            raise TimeoutError

        monkeypatch.setattr("qcl_negf_api.api.asyncio.wait_for", timeout)
        await BodyLimit(application, limit=2000000)(
            {"type": "http", "path": "/api/v1/configurator/quantum-preview"}, receive, send
        )
        assert not called and sent[0]["status"] == 504

    asyncio.run(case())


def test_real_transport_decoder_binds_literal_passport_and_raw_file_before_release():
    from qcl_negf_api.quantum_preview import decode_quantum_response

    passport = {
        "temperature_K": 70.0,
        "voltage_per_period_V": 0.0,
        "configuration_hash": "b" * 64,
        "hash_encoding": "qcl-negf-canonical-bytes-v1",
        "axes_applied": True,
    }
    plan = json.loads(PLAN)
    plan["scientific_fingerprint"] = "c" * 64
    plan["points"][0]["effective_inputs"] = passport
    frozen = json.dumps(plan)
    request = {
        **REQUEST,
        "plan": frozen,
        "plan_sha256": hashlib.sha256(frozen.encode()).hexdigest(),
    }
    body = json.dumps(request).encode()
    value = {
        "schema": "qcl-negf.quantum-preview.v1",
        "status": "available",
        "preview_id": "a" * 64,
        "payload_sha256": "b" * 64,
        "payload_digest_encoding": "qcl-negf-canonical-bytes-v1",
        "budget_policy_id": "quantum-preview-reference-v1",
        "policy_revision": 1,
        "identity": {
            "plan_sha256": request["plan_sha256"],
            "execution_id": "e",
            "point_id": "p",
            "variant_id": "v",
            "runner_source_id": "e" * 40,
            "core_source_id": "f" * 40,
            "request_sha256": hashlib.sha256(body).hexdigest(),
            "fingerprint": "a" * 64,
            "scientific_fingerprint": "c" * 64,
            "point_passport": passport,
            "runtime": {
                "runner_source_id": "e" * 40,
                "core_source_id": "f" * 40,
                "context_sha256": CONTEXT["context_sha256"],
            },
        },
        "payload": {
            "shape": [3, 2],
            "dimensions": {"Nz": 3, "Nb": 2},
            "energy_eV": [0.1, 0.2],
            "z_nm": [0.0, 1.0, 3.0],
            "band_edge_eV": [0.2, 0.3, 0.4],
            "psi_real": [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
            "psi_imag": [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
            "density_per_nm": [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]],
            "units": {"energy": "eV", "z": "nm", "psi": "nm^-1/2", "density": "nm^-1"},
            "semantics": {
                "scope": "bare_onsite_k0",
                "hartree": "zero",
                "kappa": 0,
                "interperiod_hopping": "excluded",
                "window_boundary": "Dirichlet",
            },
        },
        "assessment": {
            "SCBA": "not_performed",
            "Poisson": "not_performed",
            "scientific": "not_assessed",
        },
    }
    raw = json.dumps(value).encode() + b"\n"
    decode_quantum_response(raw, request, CONTEXT)
    with pytest.raises(ConfiguratorError):
        decode_quantum_response(
            json.dumps(
                {
                    **value,
                    "identity": {
                        **value["identity"],
                        "point_passport": {**passport, "configuration_hash": "0" * 64},
                    },
                }
            ).encode(),
            request,
            CONTEXT,
        )

    async def case():
        group = Group()
        process = Process(raw)

        async def spawn(*args, **kwargs):
            Path(args[-1]).write_bytes(raw)
            return process

        runtime = Configurator(
            settings(),
            group=group,
            spawn=spawn,
            killpg=lambda _: None,
            quantum_binding=lambda: CONTEXT,
            memory_admission=lambda: None,
        )
        assert await runtime.quantum(body) == raw
        assert process.stdin.raw == body and group.events[-1] == "remove" and not runtime.busy

    asyncio.run(case())


def test_owner_limit_refusal_retains_safe_actual_limit_and_raw_plan_identity():
    from qcl_negf_api.quantum_preview import QuantumRefusal, decode_quantum_response

    refusal = {
        "schema": "qcl-negf.quantum-preview.v1",
        "status": "error",
        "reason": "window_limit",
        "actual": 1537,
        "allowed": 1536,
        "plan_changed": False,
        "private": "/host/secret",
    }
    with pytest.raises(QuantumRefusal) as captured:
        decode_quantum_response(json.dumps(refusal).encode(), REQUEST, CONTEXT)
    assert captured.value.response["actual"] == 1537 and captured.value.response["allowed"] == 1536
    assert captured.value.response["identity"]["plan_sha256"] == REQUEST["plan_sha256"]
    assert "/host/secret" not in json.dumps(captured.value.response)


def test_hidden_namespace_boot_change_or_stale_snapshot_refuses(monkeypatch):
    from qcl_negf_api import quantum_preview as module

    pressure = "some avg10=0 avg60=0 avg300=0 total=0\nfull avg10=0 avg60=0 avg300=0 total=0\n"
    boot = "12345678-1234-1234-1234-123456789012"
    files = {
        "/proc/sys/kernel/random/boot_id": boot,
        "/proc/self/cgroup": "0::/unit/api\n",
        "/proc/meminfo": "MemTotal: 4194304 kB\nMemAvailable: 3145728 kB\n",
        "/proc/self/mountinfo": "1 0 0:1 /hidden /cg rw - cgroup2 cgroup rw\n",
    }
    for root in ("/cg/unit", "/cg"):
        for key, value in {
            "memory.current": "0",
            "memory.max": "max",
            "memory.high": "max",
            "memory.swap.current": "0",
            "memory.events": "high 0\nmax 0\noom 0\noom_kill 0\n",
            "memory.pressure": pressure,
            "cpu.max": "max 100000",
        }.items():
            files[root + "/" + key] = value
    monkeypatch.setattr(module, "_small", lambda path, maximum=65536: files[str(path)])
    monkeypatch.setattr(module.os, "sched_getaffinity", lambda _: {0})
    monkeypatch.setattr(module.os, "readlink", lambda _: "cgroup:[1]")
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")
    files["/proc/self/mountinfo"] = "1 0 0:1 / /cg rw - cgroup2 cgroup rw\n"
    calls = 0

    def changed(path, maximum=65536):
        nonlocal calls
        if str(path) == "/proc/sys/kernel/random/boot_id":
            calls += 1
            if calls > 1:
                return "87654321-1234-1234-1234-123456789012"
        return files[str(path)]

    monkeypatch.setattr(module, "_small", changed)
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")
    reads = 0

    def moved(path, maximum=65536):
        nonlocal reads
        if str(path) == "/proc/self/cgroup":
            reads += 1
            if reads > 1:
                return "0::/other/api\n"
        return files[str(path)]

    monkeypatch.setattr(module, "_small", moved)
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")
    monkeypatch.setattr(module, "_small", lambda path, maximum=65536: files[str(path)])
    links = 0

    def changed_namespace(path):
        nonlocal links
        assert path == "/proc/self/ns/cgroup"
        links += 1
        return "cgroup:[1]" if links == 1 else "cgroup:[2]"

    monkeypatch.setattr(module.os, "readlink", changed_namespace)
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")
    monkeypatch.setattr(module.os, "readlink", lambda path: "cgroup:[1]")
    ticks = 0

    def stale():
        nonlocal ticks
        ticks += 1
        return 0 if ticks == 1 else 6

    monkeypatch.setattr(module.time, "monotonic", stale)
    with pytest.raises(ConfiguratorError):
        module.memory_admission("/cg/unit")


def test_quantum_deadline_and_two_mib_flood_do_not_release_before_cleanup():
    async def case():
        group = Group()
        calls = []
        process = Process(b"x" * (2097152 + 1))

        async def spawn(*args, **kwargs):
            calls.append(args)
            return process

        runtime = Configurator(
            settings(),
            group=group,
            spawn=spawn,
            killpg=lambda _: None,
            quantum_binding=lambda: CONTEXT,
            memory_admission=lambda: None,
        )
        with pytest.raises(ConfiguratorError, match="timeout"):
            await runtime.quantum(json.dumps(REQUEST).encode(), deadline=time.monotonic() - 1)
        assert not calls and not runtime.busy
        with pytest.raises(ConfiguratorError, match="limit"):
            await runtime.quantum(json.dumps(REQUEST).encode())
        assert process.waited and group.events[-1] == "remove" and not runtime.busy

    asyncio.run(case())


def test_protected_record_refuses_writable_or_foreign_parent_before_file_open(monkeypatch):
    import stat
    from types import SimpleNamespace

    from qcl_negf_api import quantum_preview as module

    opened = []
    monkeypatch.setattr(module.os, "open", lambda *a, **k: opened.append(a))
    for owner, mode in (
        (0, stat.S_IFDIR | 0o777),
        (1000, stat.S_IFDIR | 0o755),
        (0, stat.S_IFLNK | 0o777),
    ):
        monkeypatch.setattr(
            module.Path,
            "lstat",
            lambda self, uid=owner, bits=mode: SimpleNamespace(st_uid=uid, st_mode=bits),
        )
        with pytest.raises(ConfiguratorError):
            module.protected_record()
    assert opened == []
    with pytest.raises(ConfiguratorError):
        module.protected_record("/tmp/unprivileged-labels.json")


def test_expired_work_cutoff_never_calls_binding_decode_or_admission(monkeypatch):
    from qcl_negf_api import configurator as module

    async def case():
        calls = []

        def binding():
            calls.append("binding")
            return CONTEXT

        def admission():
            calls.append("admission")

        async def spawn(*a, **k):
            calls.append("spawn")
            return Process()

        def decode(*a):
            calls.append("decode")
            return REQUEST

        monkeypatch.setattr("qcl_negf_api.quantum_preview.decode_quantum_request", decode)
        runtime = Configurator(
            settings(),
            group=Group(),
            spawn=spawn,
            killpg=lambda _: None,
            quantum_binding=binding,
            memory_admission=admission,
        )
        with pytest.raises(ConfiguratorError, match="timeout"):
            await runtime.quantum(b"not parsed", deadline=time.monotonic() - 1)
        assert calls == [] and not runtime.busy
        clock = [0.0]
        monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

        def late_binding():
            calls.append("binding")
            clock[0] = 186.0
            return CONTEXT

        runtime.quantum_binding = late_binding
        with pytest.raises(ConfiguratorError, match="timeout"):
            await runtime.quantum(b"not parsed", deadline=185.0)
        assert calls == ["binding"] and not runtime.busy
        calls.clear()
        clock[0] = 0.0
        runtime.quantum_binding = binding

        def late_decode(*a):
            calls.append("decode")
            clock[0] = 186.0
            return REQUEST

        monkeypatch.setattr("qcl_negf_api.quantum_preview.decode_quantum_request", late_decode)
        with pytest.raises(ConfiguratorError, match="timeout"):
            await runtime.quantum(b"not parsed", deadline=185.0)
        assert calls == ["binding", "decode"] and not runtime.busy

    asyncio.run(case())


def test_literal_late_body_188_is_rejected_at_185_without_adapter_work(monkeypatch):
    from qcl_negf_api import api as module

    async def case():
        clock = [0.0]
        called = []
        sent = []
        budgets = []
        monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

        async def application(*a):
            called.append("binding/admission/spawn")

        async def receive():
            clock[0] = 188.0
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def read(awaitable, seconds):
            budgets.append(seconds)
            return await awaitable

        async def send(message):
            sent.append(message)

        monkeypatch.setattr(module.asyncio, "wait_for", read)
        scope = {"type": "http", "path": "/api/v1/configurator/quantum-preview"}
        await module.BodyLimit(application, 2000000)(scope, receive, send)
        assert scope["quantum_deadline"] == 190.0 and budgets == [185.0]
        assert not called and sent[0]["status"] == 504

    asyncio.run(case())
