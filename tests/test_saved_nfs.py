"""Synthetic mount facts + real filesystem/native5 public readers; no real NFS."""

import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest
from qcl_negf_api import saved_nfs, saved_results
from qcl_negf_api.saved_results import SavedResultError
from test_saved_results import BINDING as BINDING
from test_saved_results import COMPUTER as COMPUTER
from test_saved_results import native_case as native_case
from test_saved_results import read


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
    selected = Path(__file__).resolve().parents[2]
    assert (
        Path(saved_results.__file__).resolve()
        == selected / "p09-native-portal/src/qcl_negf_api/saved_results.py"
    )
    assert (
        Path(selected_state.__file__).resolve()
        == selected / "p30-s3-results/src/qcl_negf_results/state.py"
    )
    assert (
        Path(selected_contracts.__file__).resolve()
        == selected / "p30-performance-sideband-contracts/src/qcl_negf_contracts/__init__.py"
    )
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
