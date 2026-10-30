"""Owning subprocess/JSON contract fixtures; never contact a cluster or profile."""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

UNKNOWN = {
    "schema": "qcl.cluster-projection.v2",
    "read_at": 100,
    "status": "unknown",
    "registry_generation": "cache-a",
    "hosts": [],
    "complete": True,
    "next_cursor": None,
    "coverage": {"sources": [], "reasons": [{"code": "not_measured", "source_id": "controller"}]},
    "current_context": None,
}


def owner():
    try:
        return importlib.import_module("qcl_negf_api.cluster_status")
    except ModuleNotFoundError:
        pytest.fail("missing owning ClusterStatus subprocess adapter")


def harness(tmp_path, monkeypatch, script):
    m = owner()
    executable = tmp_path / "fixed-client"
    executable.write_text("#!" + sys.executable + "\n" + script)
    executable.chmod(0o700)
    # Explicit trust fixture only: owned temporary file replaces immutable store
    # member at this one seam; production trust validation is tested separately.
    monkeypatch.setattr(m, "_immutable_client", lambda value: value)
    return m, m.ClusterStatus(SimpleNamespace(cluster_cache_executable=str(executable)))


def test_real_fixed_client_preserves_full_unknown_dto_and_exact_query(tmp_path, monkeypatch):
    script = """import base64,json,os,sys
assert len(sys.argv)==3 and sys.argv[1]=='--query-base64'
query=json.loads(base64.urlsafe_b64decode(sys.argv[2]+'='*(-len(sys.argv[2])%4)))
assert query=={{'schema':'qcl.cluster-query.v1','request_id':None,'cursor':None,'limit':64}}
assert set(os.environ)<= {{'LANG','LC_ALL','PATH'}}
print({!r})
""".format(json.dumps(UNKNOWN, separators=(",", ":")))
    m, adapter = harness(tmp_path, monkeypatch, script)

    async def case():
        assert await adapter.read() == UNKNOWN
        await adapter.close()
        with pytest.raises(m.ClusterStatusError) as observed:
            await adapter.read()
        assert observed.value.code == "cluster_cache_unavailable"

    asyncio.run(case())


@pytest.mark.parametrize(
    "raw,code",
    [
        ("{}\n", "cluster_cache_invalid"),
        ('{"schema":1,"schema":2}\n', "cluster_cache_invalid"),
        ('{"x":NaN}\n', "cluster_cache_invalid"),
        ("{}\n{}\n", "cluster_cache_invalid"),
        ("{}", "cluster_cache_invalid"),
        (
            json.dumps({**UNKNOWN, "read_at": 9007199254740992}) + "\n",
            "cluster_value_out_of_browser_range",
        ),
    ],
    ids=["shape", "duplicate", "nonfinite", "trailing", "no-LF", "unsafe-integer"],
)
def test_strict_reply_refuses_bad_bytes_and_unsafe_browser_integer(
    tmp_path, monkeypatch, raw, code
):
    m, adapter = harness(tmp_path, monkeypatch, f"import os\nos.write(1,{raw.encode()!r})\n")

    async def case():
        with pytest.raises(m.ClusterStatusError) as observed:
            await adapter.read()
        assert observed.value.code == code
        await adapter.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "script,code",
    [
        (
            'import sys\nprint(\'{"schema":"qcl.monitoring-error.v1",'
            '"reason":"stale_cursor"}\',file=sys.stderr)\nsys.exit(1)',
            "stale_cursor",
        ),
        (
            'import sys\nprint("private /etc/key detail",file=sys.stderr)\nsys.exit(1)',
            "cluster_cache_unavailable",
        ),
        (
            'import sys\nprint(\'{"schema":"qcl.monitoring-error.v1",'
            '"reason":"stale_cursor"}\',file=sys.stderr)\nprint("partial")\nsys.exit(1)',
            "cluster_cache_unavailable",
        ),
    ],
    ids=["typed-stale", "private-stderr", "partial-stdout"],
)
def test_nonzero_maps_only_clean_typed_owner_error(tmp_path, monkeypatch, script, code):
    m, adapter = harness(tmp_path, monkeypatch, script)

    async def case():
        with pytest.raises(m.ClusterStatusError) as observed:
            await adapter.read()
        assert observed.value.code == code and "private" not in str(observed.value)
        await adapter.close()

    asyncio.run(case())


@pytest.mark.parametrize("fd,size", [(1, 1048578), (2, 4097)], ids=["stdout", "stderr"])
def test_actual_child_output_is_bounded_before_acceptance(tmp_path, monkeypatch, fd, size):
    m, adapter = harness(tmp_path, monkeypatch, f'import os\nos.write({fd},b"x"*{size})\n')

    async def case():
        with pytest.raises(m.ClusterStatusError) as observed:
            await adapter.read()
        assert observed.value.code == "cluster_cache_invalid"
        await adapter.close()

    asyncio.run(case())


@pytest.mark.parametrize("trigger", ["timeout", "cancel", "close"])
def test_actual_child_group_reaped_after_timeout_cancellation_or_close(
    tmp_path, monkeypatch, trigger
):
    marker = tmp_path / "pid"
    script = (
        "import os,time,signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        f'open({str(marker)!r},"w").write(str(os.getpid()))\ntime.sleep(60)\n'
    )
    m, adapter = harness(tmp_path, monkeypatch, script)
    monkeypatch.setattr(m, "DEADLINE_SECONDS", 0.15)

    async def case():
        task = asyncio.create_task(adapter.read())
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.005)
        assert marker.exists()
        pid = int(marker.read_text())
        if trigger == "cancel":
            task.cancel()
        if trigger == "close":
            await adapter.close()
        if trigger in ("cancel", "close"):
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(m.ClusterStatusError) as observed:
                await task
            assert observed.value.code == "cluster_cache_timeout"
        await adapter.close()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(case())


def test_four_inflight_fail_fast_without_fifth_spawn(tmp_path, monkeypatch):
    marker = tmp_path / "started"
    script = (
        f'import os,time\nwith open({str(marker)!r},"a") as f:'
        'f.write(str(os.getpid())+"\\n")\ntime.sleep(60)\n'
    )
    m, adapter = harness(tmp_path, monkeypatch, script)

    async def case():
        tasks = [asyncio.create_task(adapter.read()) for _ in range(4)]
        for _ in range(100):
            if marker.exists() and len(marker.read_text().splitlines()) == 4:
                break
            await asyncio.sleep(0.005)
        with pytest.raises(m.ClusterStatusError) as observed:
            await adapter.read()
        assert observed.value.code == "cluster_cache_busy"
        assert len(marker.read_text().splitlines()) == 4
        await adapter.close()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(case())


def test_missing_or_mutable_binding_cannot_launch_subprocess(tmp_path, monkeypatch):
    m = owner()
    marker = tmp_path / "effect"
    exe = tmp_path / "mutable-client"
    exe.write_text("#!/bin/sh\ntouch " + str(marker))
    exe.chmod(0o700)

    async def case():
        for value in [None, str(exe), "/nix/store/a/../b/bin/qcl-negf-cluster-cache-status"]:
            adapter = m.ClusterStatus(SimpleNamespace(cluster_cache_executable=value))
            with pytest.raises(m.ClusterStatusError) as observed:
                await adapter.read()
            assert observed.value.code == "cluster_cache_unavailable"
            await adapter.close()
        assert not marker.exists()

    asyncio.run(case())


@pytest.mark.parametrize(
    "condition",
    [
        "good",
        "symlink",
        "foreign",
        "writable-member",
        "no-execute",
        "writable-store-without-sticky",
        "world-writable-store",
    ],
)
def test_startup_trust_requires_exact_immutable_regular_root_namespace(
    tmp_path, monkeypatch, condition
):
    m = owner()
    fake = tmp_path / "namespace"
    client = fake / "nix/store/member/bin/qcl-negf-cluster-cache-status"
    client.parent.mkdir(parents=True)
    client.write_text("#!/bin/sh\nexit 0\n")
    client.chmod(0o755)
    store = fake / "nix/store"
    store.chmod(0o1775)
    if condition == "symlink":
        client.unlink()
        client.symlink_to("/bin/true")
    if condition == "writable-member":
        client.chmod(0o775)
    if condition == "no-execute":
        client.chmod(0o644)
    if condition == "writable-store-without-sticky":
        store.chmod(0o775)
    if condition == "world-writable-store":
        store.chmod(0o1777)
    open_fd = m.os.open
    fstat = m.os.fstat
    member_fds = set()

    def local_open(path, flags, *args, **kwargs):
        descriptor = open_fd(str(fake) if path == "/" else path, flags, *args, **kwargs)
        if path == "qcl-negf-cluster-cache-status":
            member_fds.add(descriptor)
        return descriptor

    def root_fixture(fd):
        info = list(fstat(fd))
        info[4] = 1 if condition == "foreign" and fd in member_fds else 0
        return os.stat_result(info)

    # Literal inode-owner facts in a local namespace, not a native store proof.
    monkeypatch.setattr(m.os, "open", local_open)
    monkeypatch.setattr(m.os, "fstat", root_fixture)
    value = "/nix/store/member/bin/qcl-negf-cluster-cache-status"
    if condition == "good":
        assert m._immutable_client(value) == value
    else:
        with pytest.raises((OSError, ValueError)):
            m._immutable_client(value)


def test_query_bound_precedes_spawn(tmp_path, monkeypatch):
    m, adapter = harness(tmp_path, monkeypatch, 'raise RuntimeError("must not execute")\n')

    async def case():
        for query in [
            {"limit": True},
            {"limit": 0},
            {"cursor": "a" * 8192},
            {"cursor": "bad/path"},
        ]:
            with pytest.raises(m.ClusterStatusError) as observed:
                await adapter.read(**query)
            assert observed.value.code == "cluster_cache_query_invalid"
        await adapter.close()

    asyncio.run(case())


def test_spawn_cancellation_still_reaps_late_owned_child(tmp_path, monkeypatch):
    marker = tmp_path / "spawn-pid"
    script = f'import os,time\nopen({str(marker)!r},"w").write(str(os.getpid()))\ntime.sleep(60)\n'
    m, adapter = harness(tmp_path, monkeypatch, script)
    real_spawn = m.asyncio.create_subprocess_exec

    async def case():
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed(*a, **kw):
            process = await real_spawn(*a, **kw)
            started.set()
            await release.wait()
            return process

        monkeypatch.setattr(m.asyncio, "create_subprocess_exec", delayed)
        task = asyncio.create_task(adapter.read())
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await adapter.close()
        if marker.exists():
            with pytest.raises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)

    asyncio.run(case())


def test_unconfirmed_cleanup_closes_admission_instead_of_repeating_children(tmp_path, monkeypatch):
    marker = tmp_path / "count"
    script = (
        f"from pathlib import Path\np=Path({str(marker)!r})\n"
        'p.write_text(p.read_text()+"x" if p.exists() else "x")\n'
        f"print({json.dumps(UNKNOWN)!r})\n"
    )
    m, adapter = harness(tmp_path, monkeypatch, script)

    async def unconfirmed(*args):
        # Child already exited/reaped by real read; isolate lost confirmation
        # without creating a fixture orphan or changing actual process ownership.
        return False

    monkeypatch.setattr(adapter, "_cleanup", unconfirmed)

    async def case():
        for _ in range(2):
            with pytest.raises(m.ClusterStatusError) as observed:
                await adapter.read()
            assert observed.value.code == "cluster_cache_unavailable"
        assert marker.read_text() == "x"
        await adapter.close()

    asyncio.run(case())


def test_response_validation_cannot_accept_after_absolute_deadline(tmp_path, monkeypatch):
    import time

    m, adapter = harness(tmp_path, monkeypatch, f"print({json.dumps(UNKNOWN)!r})\n")
    real_response = m._response

    def slow_validation(raw):
        value = real_response(raw)
        time.sleep(0.35)  # CPU/parse boundary cannot rely on another await.
        return value

    monkeypatch.setattr(m, "_response", slow_validation)
    monkeypatch.setattr(m, "DEADLINE_SECONDS", 0.3)

    async def case():
        with pytest.raises(m.ClusterStatusError) as observed:
            await adapter.read()
        assert observed.value.code == "cluster_cache_timeout"
        await adapter.close()

    asyncio.run(case())


def _live_response(numeric_literal):
    # Read the existing public synthetic Platform example, not a copied schema
    # or private runtime cache. Integral float variants are the reviewer's
    # independent schema-valid host-memory cases.
    example = (
        Path(__file__).resolve().parents[2]
        / "qcl-negf-platform"
        / "examples/cluster-projection.json.example"
    )
    value = json.loads(example.read_text())
    value["hosts"][0]["native"]["host_memory"]["total_bytes"] = "__numeric__"
    return (json.dumps(value).replace('"__numeric__"', numeric_literal) + "\n").encode()


@pytest.mark.parametrize("literal", ["9007199254740992.0", "1e18"])
def test_nested_owner_dto_integral_float_range_refusal(literal):
    m = owner()
    with pytest.raises(m.ClusterStatusError) as observed:
        m._response(_live_response(literal))
    assert observed.value.code == "cluster_value_out_of_browser_range"


@pytest.mark.parametrize(
    "literal",
    ["9007199254740991", "9007199254740991.0", "123.0", "1e3", "1.25", "-1.25", "true", "false"],
)
def test_numeric_transport_guard_preserves_safe_values_without_coercion(literal):
    m = owner()
    # Fractions/bools here exercise transport semantics, not the owning byte
    # field's integer schema. Full schema validation stays in Platform.
    raw = _live_response(literal)
    expected = json.loads(raw)
    result = m._response(raw)
    value = result["hosts"][0]["native"]["host_memory"]["total_bytes"]
    original = expected["hosts"][0]["native"]["host_memory"]["total_bytes"]
    assert result == expected and type(value) is type(original)


@pytest.mark.parametrize("literal", ["9007199254740992.0", "1e18"])
def test_actual_nested_child_unsafe_number_reaches_http503(tmp_path, monkeypatch, literal):
    from fastapi.testclient import TestClient

    from qcl_negf_api.api import create_app
    from qcl_negf_api.settings import Settings

    raw = _live_response(literal)
    _, adapter = harness(tmp_path, monkeypatch, f"import os\nos.write(1,{raw!r})\n")

    class Saved:
        async def close(self):
            pass

    token = "a" * 64
    code = "10dd2a4c-031b-425c-90b8-2f7ea564d313"
    app = create_app(
        Settings(token, (code,)), SimpleNamespace(), saved_results=Saved(), cluster_status=adapter
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/cluster/status", headers={"Authorization": "Bearer " + token}
        )
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "cluster_value_out_of_browser_range"}}
        assert response.headers["cache-control"] == "no-store"
