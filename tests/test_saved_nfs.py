"""Synthetic mount facts + real filesystem/native5 public readers; no real NFS."""

import os
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest
from test_saved_results import BINDING as BINDING
from test_saved_results import COMPUTER as COMPUTER
from test_saved_results import catalog_case as catalog_case
from test_saved_results import catalog_read, read
from test_saved_results import history_case as history_case
from test_saved_results import native_case as native_case

from qcl_negf_api import saved_nfs, saved_results
from qcl_negf_api.saved_results import SavedResultError


def _qualified_source_roots(modules):
    """Portable parent/child check; an owning run can inject frozen source roots."""
    portal = Path(__file__).resolve().parents[1] / "src"
    expected = {"qcl_negf_api": portal}
    for package, variable in [
        ("qcl_negf_results", "QCL_NEGF_TEST_RESULTS_SOURCE_ROOT"),
        ("qcl_negf_contracts", "QCL_NEGF_TEST_CONTRACTS_SOURCE_ROOT"),
    ]:
        if variable in os.environ:
            expected[package] = Path(os.environ[variable]).resolve()
    for name, (filename, _) in modules.items():
        package = name.split(".")[0]
        if package in expected:
            module_path = Path(*name.split("."))
            leaf = "__init__.py" if "." not in name else module_path.name + ".py"
            relative = module_path / leaf if "." not in name else module_path.parent / leaf
            assert Path(filename) == expected[package] / relative


@pytest.mark.parametrize(
    "changed",
    [
        "state_id",
        "sequence",
        "identity",
        "manifest",
        "invocation",
        "O1path",
        "O1bytes",
        "O1sha",
        "O1schema",
        "O1media",
        "order",
    ],
)
def test_R1_child_reply_full_binding_checked_again_in_parent(catalog_case, changed):
    c = catalog_case(finals=2, gaps=False)
    page = catalog_read(c)

    def worker(op, request, spool, *args):
        result = saved_results._read_request({"operation": op, **request}, spool)
        if changed == "order":
            result["points"].reverse()
        else:
            view = result["points"][0]["view"]
            if changed == "state_id":
                view["source_state"]["state_id"] = "foreign"
            elif changed == "sequence":
                view["source_state"]["state_sequence"] = "2"
            elif changed == "identity":
                view["identity"]["attempt"] = "8"
            elif changed == "manifest":
                view["source_state"]["manifest"]["media_type"] = "application/foreign"
            elif changed == "invocation":
                view["invocation"]["source_epoch"] = "foreign"
            else:
                key = {
                    "O1path": "path",
                    "O1bytes": "bytes",
                    "O1sha": "sha256",
                    "O1schema": "schema",
                    "O1media": "media_type",
                }[changed]
                view["artifact_ref"][key] = "foreign"
        return result

    c.adapter.worker = worker
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})


@pytest.mark.parametrize("mode", ["success", "failure_second", "cancel_open_owner"])
def test_R1_actual_qualified_page_child_close_kill_and_reap(
    catalog_case, tmp_path, monkeypatch, record_property, mode
):
    import asyncio
    import hashlib
    import importlib
    import json

    c = catalog_case(finals=2, gaps=False)
    if mode == "failure_second":
        from test_saved_results import _catalog_repin

        c.catalog["binding"]["points"][1]["source"]["observations"]["sha256"] = "0" * 64
        _catalog_repin(c)
    page = catalog_read(c)
    modules = {}
    for name in (
        "qcl_negf_api.saved_results",
        "qcl_negf_api.saved_nfs",
        "qcl_negf_results.state",
        "qcl_negf_results.saved_views",
        "qcl_negf_contracts.messages",
    ):
        m = importlib.import_module(name)
        p = Path(m.__file__).resolve()
        modules[name] = (str(p), hashlib.sha256(p.read_bytes()).hexdigest())
    _qualified_source_roots(modules)
    wrapper = tmp_path / "page-child.py"
    marker = tmp_path / "opened"
    code = (
        "import hashlib,importlib,signal\nfrom pathlib import Path\n"
        "from qcl_negf_api import saved_nfs,saved_results\nexpected="
        + repr(modules)
        + "\nfor name,(path,digest) in expected.items():\n"
        " m=importlib.import_module(name)\n"
        " assert str(Path(m.__file__).resolve())==path\n"
        " assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest\n"
    )
    code += (
        "saved_nfs.ROOT="
        + repr(str(c.root))
        + "\nsaved_nfs._mountinfo=lambda:"
        + repr(c.mount)
        + "\n"
    )
    if mode == "cancel_open_owner":
        code += (
            "from qcl_negf_results.state import StateReader\noriginal=StateReader.__init__\n"
            "def held(self,*a,**k):\n original(self,*a,**k)\n Path("
            + repr(str(marker))
            + ").write_text('open')\n signal.pause()\nStateReader.__init__=held\n"
        )
    code += "raise SystemExit(saved_results._main())\n"
    wrapper.write_text(code)
    original = saved_results.subprocess.Popen
    children = []

    def spawn(args, **kwargs):
        assert args[1:3] == ["-m", "qcl_negf_api.saved_results"]
        assert all(
            str(Path(path).parent.parent) in kwargs["env"]["PYTHONPATH"]
            for path, _ in modules.values()
        )
        child = original([args[0], str(wrapper), args[-1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(saved_results.subprocess, "Popen", spawn)
    c.adapter.worker = saved_results._worker
    query = {"page_ref": page["pagination"]["page_ref"]}
    if mode == "success":
        value = catalog_read(c, "brief", query)
        assert value["credits"]["stage_share"] == 33554432
        record_property("r1_actual_child_page_golden", json.dumps(value))
    elif mode == "failure_second":
        with pytest.raises(SavedResultError, match="corrupt_result"):
            catalog_read(c, "brief", query)
    else:

        async def cancel():
            task = asyncio.create_task(c.adapter.read("brief", c.selection, query))
            for _ in range(400):
                if marker.exists():
                    break
                await asyncio.sleep(0.005)
            assert marker.exists()
            with pytest.raises(SavedResultError, match="busy"):
                await c.adapter.read("sources", c.selection, {})
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(200):
                if c.adapter.active is None:
                    break
                await asyncio.sleep(0.005)
            assert c.adapter.active is None and not c.adapter.busy

        asyncio.run(cancel())
    assert len(children) == 1
    assert all(p.poll() is not None and p.stdout.closed and p.stderr.closed for p in children)
    record_property(
        "r1_owned_child",
        json.dumps(
            {
                "mode": mode,
                "children": [
                    {"pid": p.pid, "returncode": p.returncode, "reaped": p.poll() is not None}
                    for p in children
                ],
                "qualified_modules": modules,
                "native_mount": "SIMULATED_RO_NOT_NATIVE",
            }
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        "binding",
        "state",
        "source",
        "raw",
        "point",
        "storage",
        "absolute",
        "absent",
        "binding_integrity",
        "readback_integrity",
    ],
)
def test_wrong_original_pin_or_storage_never_opens_data(native_case, change):
    query = {}
    if change == "binding":
        native_case.dto["native_binding"] = {
            **native_case.dto["native_binding"],
            "node_uuid": native_case.selection["calcjob_uuid"],
        }
    elif change == "state":
        query["state_manifest_sha256"] = "f" * 64
    elif change == "source":
        native_case.source["selected_state"]["identity"]["point_id"] = "foreign"
    elif change == "raw":
        native_case.dto["result_utf8"] = "{ }"
    elif change == "point":
        native_case.dto["point"] = {**native_case.dto["point"], "point_id": "other"}
    elif change == "storage":
        native_case.dto["binding"]["storage"]["hold_ref"] = "other"
    elif change == "absolute":
        native_case.source["artifact_root"] = "/etc"
    elif change == "binding_integrity":
        native_case.dto["binding"]["integrity"] = {}
    elif change == "readback_integrity":
        native_case.dto["integrity"]["native_contexts"] = "verified"
    else:
        native_case.source.update(
            kind="absent",
            selected_state=None,
            diagnostics=None,
            observations=None,
            relation=None,
            reason="not_recorded",
        )
    native_case.adapter.worker = lambda *a: pytest.fail("invalid owner metadata opened child")
    with pytest.raises(SavedResultError):
        read(native_case, "manifest", query)


@pytest.mark.parametrize("change", ["submount", "rw", "symlink", "replace", "mount_change"])
def test_actual_domain_refuses_unsafe_subtree_and_lifetime_change(native_case, monkeypatch, change):
    mount = native_case.mount
    if change == "submount":
        mount += f"11 10 0:42 /nested {native_case.data}/unused ro - nfs4 synthetic:/results ro\n"
    if change == "rw":
        mount = mount.replace(" ro -", " rw -")
    if change in ("submount", "rw"):
        monkeypatch.setattr(saved_nfs, "_mountinfo", lambda: mount)
    if change == "symlink":
        native_case.data.rename(native_case.root / "original")
        native_case.data.symlink_to(native_case.root / "original", target_is_directory=True)
    if change in ("replace", "mount_change"):
        from qcl_negf_results.saved_views import describe_saved_views

        def changed(*args, **kwargs):
            result = describe_saved_views(*args, **kwargs)
            if change == "replace":
                native_case.data.rename(native_case.root / "previous")
                native_case.data.mkdir()
            else:
                monkeypatch.setattr(
                    saved_nfs,
                    "_mountinfo",
                    lambda: mount + "12 1 0:55 / /unrelated ro - tmpfs tmpfs ro\n",
                )
            return result

        monkeypatch.setattr("qcl_negf_results.saved_views.describe_saved_views", changed)
    with pytest.raises((SavedResultError, OSError)):
        read(native_case, "manifest")


def test_full_state_echo_must_match_not_only_manifest_hash(native_case):
    state = deepcopy(native_case.state)
    state["state_id"] = "foreign"
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        read(native_case, "slice", dict(source_state=state, view_id="density", selection=[0]))


def test_checkpoint_source_has_diagnostics_but_no_synthetic_final_observation(native_case):
    native_case.source.update(
        kind="checkpoint", diagnostics=None, observations=None, reason="interrupted"
    )
    assert read(native_case, "diagnostics")["view"]["source"] == native_case.state
    with pytest.raises(SavedResultError, match="capability_unavailable"):
        read(native_case, "manifest")


def test_real_isolated_worker_uses_selected_imports_and_closes_actual_child(
    native_case, tmp_path, monkeypatch
):
    wrapper = tmp_path / "synthetic_child.py"
    import hashlib

    import qcl_negf_contracts as selected_contracts
    import qcl_negf_results.state as selected_state

    modules = {
        module.__name__: (
            str(Path(module.__file__).resolve()),
            hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
        )
        for module in (saved_results, selected_state, selected_contracts)
    }
    _qualified_source_roots(modules)
    assert (
        modules[selected_state.__name__][1]
        == "732ec8585dc5a16aa3b841d6f43f8cfe5d7417342dcd5a38cbfedea4654de596"
    )
    wrapper.write_text(
        "import hashlib,importlib\nfrom pathlib import Path\n"
        "from qcl_negf_api import saved_nfs,saved_results\n"
        + "expected="
        + repr(modules)
        + "\n"
        + "for name,(path,digest) in expected.items():\n"
        " module=importlib.import_module(name)\n"
        " assert str(Path(module.__file__).resolve())==path\n"
        " assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()==digest\n"
        + "saved_nfs.ROOT="
        + repr(str(native_case.root))
        + "\nsaved_nfs._mountinfo=lambda:"
        + repr(native_case.mount)
        + "\nraise SystemExit(saved_results._main())\n"
    )
    original = saved_results.subprocess.Popen
    children = []

    def spawn(args, **kwargs):
        assert args[1:3] == ["-m", "qcl_negf_api.saved_results"]
        child = original([args[0], str(wrapper), args[-1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(saved_results.subprocess, "Popen", spawn)
    native_case.adapter.worker = saved_results._worker
    answer = read(native_case, "manifest")
    assert answer["view"]["artifact_ref"] == native_case.observations
    slice_value = read(
        native_case,
        "slice",
        dict(
            source_state=native_case.state,
            view_id="density",
            selection=[dict(start=0, stop=3, step=1)],
        ),
    )
    assert slice_value["source_state"] == native_case.state
    assert read(native_case, "diagnostics")["view"]["source"] == native_case.state
    assert len(children) == 3
    assert all(
        child.poll() is not None and child.stdout.closed and child.stderr.closed
        for child in children
    )


def test_worker_cancel_kills_and_reaps_owned_child(native_case, tmp_path, monkeypatch):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(5)\n")
    original = saved_results.subprocess.Popen
    children = []

    def spawn(args, **kwargs):
        child = original([args[0], str(script)], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(saved_results.subprocess, "Popen", spawn)
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    try:
        with pytest.raises(SavedResultError, match="budget_exceeded"):
            saved_results._worker("manifest", {}, tmp_path, cancelled, time.monotonic() + 2)
    finally:
        timer.join()
    assert len(children) == 1 and children[0].poll() is not None


def test_domain_initialization_cleanup_cannot_mask_primary_selection_failure(
    native_case, monkeypatch
):
    original = saved_nfs._NativeDomain.close

    def check(self):
        raise SavedResultError("identity_mismatch")

    def close(self):
        original(self)
        raise SavedResultError("unavailable_storage")

    monkeypatch.setattr(saved_nfs._NativeDomain, "check", check)
    monkeypatch.setattr(saved_nfs._NativeDomain, "close", close)
    with pytest.raises(SavedResultError, match="identity_mismatch") as caught:
        read(native_case, "manifest")
    assert caught.value.cleanup_unconfirmed and native_case.adapter.closed


def test_metadata_pins_bind_next_reader_and_foreign_full_state_is_refused(native_case):
    import asyncio

    from test_saved_results import metadata_read

    source = metadata_read(native_case)
    query = dict(
        provider="native_nfs",
        point_id=source["point"]["point_id"],
        native_binding_uuid=source["retained"]["native_binding"]["node_uuid"],
        state_manifest_sha256=source["source_state"]["manifest"]["sha256"],
    )

    async def run():
        return await native_case.adapter.read("manifest", native_case.selection, query)

    result = asyncio.run(run())
    assert result["source_state"] == source["source_state"]
    wrong = deepcopy(source["source_state"])
    wrong["state_id"] = "stale"
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        read(native_case, "slice", dict(source_state=wrong, view_id="density", selection=[0]))


@pytest.mark.parametrize("mode", ["success", "failure_second", "cancel_first", "cancel_second"])
def test_P10_actual_pair_child_sources_kill_reap(
    history_case, tmp_path, monkeypatch, record_property, mode
):
    import asyncio
    import hashlib
    import importlib
    import json

    from test_saved_results import history_read, history_selection

    c = history_case
    left, right = history_selection(c), history_selection(c, 1)
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=[], reason="NEW source/L synthetic only")
    pair = dict(left=left, right=right, comparability=assessment)
    if mode == "failure_second":
        (c.data / "p1" / "manifest.json").write_bytes(b"{}")
    modules = {}
    for name in (
        "qcl_negf_api.saved_results",
        "qcl_negf_api.saved_nfs",
        "qcl_negf_results.state",
        "qcl_negf_results.live_views",
        "qcl_negf_results._capture_io",
        "qcl_negf_contracts.messages",
    ):
        p = Path(importlib.import_module(name).__file__).resolve()
        modules[name] = (str(p), hashlib.sha256(p.read_bytes()).hexdigest())
    _qualified_source_roots(modules)
    marker = tmp_path / "opened"
    code = (
        "import importlib,hashlib,signal\nfrom pathlib import Path\n"
        "from qcl_negf_api import saved_nfs,saved_results\nexpected=" + repr(modules) + "\n"
        "for name,(path,digest) in expected.items():\n"
        " p=Path(importlib.import_module(name).__file__).resolve()\n"
        " assert str(p)==path and hashlib.sha256(p.read_bytes()).hexdigest()==digest\n"
        "saved_nfs.ROOT="
        + repr(str(c.root))
        + "\nsaved_nfs._mountinfo=lambda:"
        + repr(c.mount)
        + "\n"
    )
    if mode.startswith("cancel"):
        target = "p0" if mode == "cancel_first" else "p1"
        code += (
            "from qcl_negf_results.state import StateReader\noriginal=StateReader.__init__\n"
            "def held(self,*a,**k):\n original(self,*a,**k)\n"
            " if self.root.name==" + repr(target) + ":\n"
            "  Path(" + repr(str(marker)) + ').write_text("open")\n  signal.pause()\n'
            "StateReader.__init__=held\n"
        )
    code += "raise SystemExit(saved_results._main())\n"
    wrapper = tmp_path / "history-child.py"
    wrapper.write_text(code)
    original = saved_results.subprocess.Popen
    children = []
    starts = []

    def spawn(args, **kwargs):
        child = original([args[0], str(wrapper), args[-1]], **kwargs)
        children.append(child)
        starts.append(Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        return child

    monkeypatch.setattr(saved_results.subprocess, "Popen", spawn)
    c.adapter.worker = saved_results._worker
    if mode == "success":
        result = history_read(c, "compare-history", pair)
        assert result["view"]["left"]["observations"] == "4"
        record_property("p10_actual_child_golden", json.dumps(result))
    elif mode == "failure_second":
        with pytest.raises(SavedResultError):
            history_read(c, "compare-history", pair)
    else:

        async def scenario():
            task = asyncio.create_task(c.adapter.read("compare-history", c.selection, pair))
            for _ in range(600):
                if marker.exists():
                    break
                await asyncio.sleep(0.005)
            assert marker.exists()
            with pytest.raises(SavedResultError, match="busy"):
                await c.adapter.read("derive-history", c.selection, {})
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(200):
                if c.adapter.active is None:
                    break
                await asyncio.sleep(0.005)
            assert c.adapter.active is None and not c.adapter.busy

        asyncio.run(scenario())
    assert len(children) == 1
    assert all(p.poll() is not None and p.stdout.closed and p.stderr.closed for p in children)
    record_property(
        "p10_child_source_receipt",
        json.dumps(
            dict(
                mode=mode,
                modules=modules,
                children=[
                    dict(
                        pid=p.pid,
                        start_ticks=s,
                        returncode=p.returncode,
                        reaped=p.poll() is not None,
                    )
                    for p, s in zip(children, starts, strict=True)
                ],
                qualification="SOURCE/L simulated RO facts, no installed/NFS/scientific acceptance",
            )
        ),
    )


@pytest.mark.parametrize("change", ["state", "locator_epoch", "count", "credit"])
def test_P10_parent_rechecks_child_full_source_result_and_epoch_ledger(history_case, change):
    from test_saved_results import history_read

    c = history_case

    def worker(op, request, spool, *args):
        value = saved_results._read_request({"operation": op, **request}, spool)
        if change == "state":
            value["view"]["source"]["identity"]["model_sha256"] = "0" * 64
        elif change == "locator_epoch":
            value["view"]["maximum"]["locator"]["source_epoch"] = "foreign"
        elif change == "count":
            value["view"]["observations"] = 4
        else:
            value["credits"]["epochs"][2]["counters"]["bytes_read_total"] += 1
        return value

    c.adapter.worker = worker
    with pytest.raises(SavedResultError):
        history_read(c)


def test_P10_both_public_readers_close_before_any_domain(history_case, monkeypatch):
    from qcl_negf_results.state import StateReader
    from test_saved_results import history_read, history_selection

    c = history_case
    closed = []
    reader_close = StateReader.close
    domain_close = saved_nfs._NativeDomain.close

    def close_reader(self):
        reader_close(self)
        closed.append(("reader", self.root.name))

    def close_domain(self):
        assert [x[0] for x in closed[:2]] == ["reader", "reader"]
        domain_close(self)
        closed.append(("domain", self.root.name))

    monkeypatch.setattr(StateReader, "close", close_reader)
    monkeypatch.setattr(saved_nfs._NativeDomain, "close", close_domain)
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=[], reason="NEW lifetime control")
    history_read(
        c,
        "compare-history",
        dict(left=history_selection(c), right=history_selection(c, 1), comparability=assessment),
    )
    assert closed == [("reader", "p0"), ("reader", "p1"), ("domain", "p1"), ("domain", "p0")]
