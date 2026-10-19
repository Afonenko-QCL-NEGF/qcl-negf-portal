"""Tiny public asset-identity fixtures; no real npm/compiler/build subprocesses.

The Python receipt writer is an independent oracle. Node tests import the real
receipt module and inject only its documented command runner, retaining actual
filesystem cleanup, inventories, publication and verification.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = ".qcl-negf-frontend-v1.json"
SCRIPT = "node tools/asset-receipt.mjs build"
COMMANDS = [
    ["node", "node_modules/typescript/bin/tsc", "--noEmit"],
    ["node", "node_modules/vite/bin/vite.js", "build", "--mode", "production"],
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def inventory(root, exclude=()):
    rows = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in exclude or not path.is_file():
            continue
        data = path.read_bytes()
        rows.append({"path": relative, "bytes": len(data), "sha256": digest(data)})
    return sorted(rows, key=lambda row: row["path"].encode())


def fixture(area, mode="source-static", real_tool=False):
    portal = area / "portal"
    frontend = portal / "frontend"
    put(portal / "pyproject.toml", '[project]\nname = "qcl-negf-api"\n')
    put(portal / "src/qcl_negf_api/__init__.py", "")
    package = {"name": "qcl-negf-portal", "private": True, "type": "module",
               "scripts": {"build": SCRIPT}}
    put(frontend / "package.json", canonical(package))
    lock = {"lockfileVersion": 3, "packages": {
        "node_modules/typescript": {"version": "5.9.3"},
        "node_modules/vite": {"version": "7.3.6"}}}
    put(frontend / "package-lock.json", canonical(lock))
    out_dir = "../src/qcl_negf_api/static" if mode == "source-static" else "dist"
    put(frontend / "vite.config.ts",
        f'export default {{build: {{outDir: "{out_dir}", emptyOutDir: true}}}};\n')
    put(frontend / "tsconfig.json", "{}\n")
    put(frontend / "index.html", '<div id="root"></div>\n')
    put(frontend / "src/main.tsx", 'document.title = "public fixture";\n')
    put(frontend / "tools/asset-receipt.mjs", "// public fixture generator identity\n")
    if real_tool:
        tool = ROOT / "frontend/tools/asset-receipt.mjs"
        if not tool.is_file():
            raise AssertionError("DEV20 receipt generator is not implemented")
        shutil.copyfile(tool, frontend / "tools/asset-receipt.mjs")
    for name, version in (("typescript", "5.9.3"), ("vite", "7.3.6")):
        put(frontend / f"node_modules/{name}/package.json", canonical({"version": version}))
    output = portal / "src/qcl_negf_api/static" if mode == "source-static" else frontend / "dist"
    payload(output, "old")
    return portal, frontend, output


def payload(output, tag):
    put(output / "index.html", f"<html>{tag}</html>\n")
    put(output / "assets/app.js", f'console.log("{tag}");\n')
    put(output / "assets/app.css", "body { color: #123; background: white; }\n")


def receipt(frontend, output, mode="source-static"):
    recipe = {"id": "qcl-negf.frontend-build.v1", "commands": COMMANDS,
              "output_root": "../src/qcl_negf_api/static" if mode == "source-static" else "dist",
              "node_env": "production", "output_reset": "clean-selected-root-v1"}
    inputs = inventory(frontend, ("node_modules", "dist"))
    outputs = inventory(output, (STAMP,))
    value = {"format": "qcl-negf.frontend-receipt.v1", "mode": mode,
             "recipe": recipe, "recipe_sha256": digest(canonical(recipe)),
             "toolchain": {"node": "24.1.0", "npm": "11.3.0",
                           "typescript": "5.9.3", "vite": "7.3.6"},
             "inputs": inputs, "inputs_sha256": digest(canonical(inputs)),
             "outputs": outputs, "outputs_sha256": digest(canonical(outputs))}
    put(output / STAMP, canonical(value) + b"\n")
    return value


class ReceiptFixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "qcl_fixture_hatch_build", ROOT / "hatch_build.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix="qcl-receipt-")
        self.addCleanup(self.area.cleanup)
        self.portal, self.frontend, self.output = fixture(Path(self.area.name))
        receipt(self.frontend, self.output)

    def wheel(self, version="standard", target="wheel"):
        # Real installed Hatchling interface; no fake module or target package import.
        hook = self.module.CustomBuildHook(str(self.portal), {}, None, None, "dist", target)
        data = {}
        hook.initialize(version, data)
        return data

    def refuses(self, reason):
        with self.assertRaisesRegex(RuntimeError, f"QCL_FRONTEND_RECEIPT_INVALID:{reason};"):
            self.wheel()


class WheelReceiptTests(ReceiptFixtureCase):
    def test_fresh_independent_receipt_is_accepted(self):
        self.assertEqual(self.wheel(), {})

    def test_wheel_rejects_stale_frontend_inputs(self):
        put(self.frontend / "src/main.tsx", "// changed source; old index and JS remain\n")
        self.refuses("inputs")

    def test_wheel_rejects_changed_asset_bytes(self):
        put(self.output / "assets/app.js", "// changed emitted bytes\n")
        self.refuses("outputs")

    def test_wheel_rejects_changed_lock(self):
        put(self.frontend / "package-lock.json", '{"lockfileVersion":3,"packages":{}}')
        with self.assertRaisesRegex(
            RuntimeError, "QCL_FRONTEND_RECEIPT_INVALID:(inputs|toolchain);"
        ):
            self.wheel()

    def test_missing_receipt_cannot_adopt_legacy_payload(self):
        (self.output / STAMP).unlink()
        self.refuses("missing")

    def test_missing_output_is_refused(self):
        (self.output / "assets/app.js").unlink()
        self.refuses("outputs")

    def test_extra_hidden_output_is_refused(self):
        put(self.output / ".unrecorded", "extra bytes")
        self.refuses("outputs")

    def test_input_rename_changes_identity(self):
        (self.frontend / "src/main.tsx").rename(self.frontend / "src/renamed.tsx")
        self.refuses("inputs")

    def test_all_public_inputs_including_hidden_and_nested_dist_are_bound(self):
        for relative in (".public-input", "tests/check.ts", "public/logo.svg", "src/dist/entry.ts"):
            with self.subTest(relative=relative):
                path = self.frontend / relative
                put(path, "public source\n")
                self.refuses("inputs")
                path.unlink()

    def test_malformed_incomplete_duplicate_and_noncanonical_receipts_fail_closed(self):
        value = json.loads((self.output / STAMP).read_bytes())
        missing = dict(value)
        missing.pop("outputs")
        boolean_size = json.loads(canonical(value))
        boolean_size["outputs"][0]["bytes"] = True
        for raw in (b"{", canonical(missing) + b"\n", canonical(value) + b" \n",
                    b'{"format":"duplicate",' + canonical(value)[1:] + b"\n",
                    canonical(boolean_size) + b"\n"):
            with self.subTest(raw=raw[:40]):
                put(self.output / STAMP, raw)
                self.refuses("schema")

    def test_recipe_digest_and_toolchain_cannot_be_relabelled(self):
        original = json.loads((self.output / STAMP).read_bytes())
        for key, reason in (("recipe_sha256", "recipe"), ("inputs_sha256", "inputs"),
                            ("outputs_sha256", "outputs")):
            with self.subTest(key=key):
                value = dict(original)
                value[key] = "0" * 64
                put(self.output / STAMP, canonical(value) + b"\n")
                self.refuses(reason)

    def test_lock_toolchain_version_mismatch_is_refused(self):
        value = json.loads((self.output / STAMP).read_bytes())
        value["toolchain"]["typescript"] = "5.9.2"
        put(self.output / STAMP, canonical(value) + b"\n")
        self.refuses("toolchain")

    def test_editable_route_does_not_claim_release_asset_identity(self):
        shutil.rmtree(self.frontend)
        shutil.rmtree(self.output)
        self.assertEqual(self.wheel(version="editable"), {})
        self.assertEqual(self.wheel(target="sdist"), {})

    def test_symlink_output_is_refused_without_touching_foreign_bytes(self):
        foreign = Path(self.area.name) / "foreign.js"
        put(foreign, "foreign sentinel\n")
        (self.output / "assets/app.js").unlink()
        (self.output / "assets/app.js").symlink_to(foreign)
        self.refuses("unsafe-path")
        self.assertEqual(foreign.read_bytes(), b"foreign sentinel\n")

    def test_nix_projection_is_exact_and_omitted_nested_dist_row_fails_closed(self):
        # Generator sees the patched, projected Nix source; hook sees full Portal source.
        original = (self.frontend / "vite.config.ts").read_bytes()
        put(self.frontend / "vite.config.ts",
            original.replace(b"../src/qcl_negf_api/static", b"dist"))
        receipt(self.frontend, self.output, "nix-dist")
        put(self.frontend / "vite.config.ts", original)
        self.assertEqual(self.wheel(), {})
        put(self.frontend / "src/dist/entry.ts", "// Nix projection omitted this source row\n")
        self.refuses("inputs")


# Runner owns only synthetic command results. Every cleanup/hash/write/verify is production code.
NODE_HARNESS = r'''
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {pathToFileURL} from "node:url";
const [front, output, foreign, behavior] = process.argv.slice(1);
const tool = pathToFileURL(path.join(front,"tools/asset-receipt.mjs"));
const {buildFrontend, verifyFrontend} = await import(tool);
let calls = [];
let emptyBeforeCompiler = false;
const runner = async (argv, context) => {
  calls.push(argv);
  assert.equal(context.cwd, front);
  if (argv[0] === "npm" && argv[1] === "--version") {
    return {exitCode:0, stdout:"11.3.0\n"};
  }
  assert.equal(context.env.NODE_ENV, "production");
  assert.equal(argv[0], process.execPath);
  if (argv[1] === "node_modules/typescript/bin/tsc") {
    assert.deepEqual(argv.slice(2), ["--noEmit"]);
    assert.deepEqual(await fs.readdir(output), [], "old selected payload/stamp survived reset");
    emptyBeforeCompiler = true;
    if (behavior === "tsc-fail") return {exitCode:17, stdout:"public compiler failure"};
  } else if (argv[1] === "node_modules/vite/bin/vite.js") {
    assert.deepEqual(argv.slice(2), ["build","--mode","production"]);
    assert.ok(emptyBeforeCompiler);
    if (behavior === "vite-fail") return {exitCode:19, stdout:"public build failure"};
    if (behavior === "fresh" || behavior === "misdirected" || behavior === "mutate") {
      const destination = behavior === "misdirected" ? path.join(foreign,"other-output") : output;
      await fs.mkdir(path.join(destination,"assets"), {recursive:true});
      await fs.writeFile(path.join(destination,"index.html"), "<html>new</html>\n");
      await fs.writeFile(path.join(destination,"assets/app.js"), "console.log('new');\n");
      if (behavior === "mutate") {
        await fs.appendFile(path.join(front,"src/main.tsx"), "// changed during build\n");
      }
    }
  } else throw new Error("unexpected actual command " + JSON.stringify(argv));
  return {exitCode:0, stdout:""};
};
let error = null;
try {await buildFrontend(front, runner);} catch (caught) {error = String(caught);}
if (behavior === "fresh") {
  assert.equal(error, null);
  await verifyFrontend(front);
  assert.equal(await fs.readFile(path.join(output,"index.html"),"utf8"),"<html>new</html>\n");
} else {
  assert.ok(error, "build accepted a failing/unsafe/nonproducing runner");
  const reason = behavior === "unsafe" ? "unsafe-path"
    : behavior === "mutate" ? "inputs" : "outputs";
  if (!["tsc-fail","vite-fail"].includes(behavior)) assert.match(error, new RegExp(reason));
  if (behavior !== "unsafe") {
    await assert.rejects(fs.stat(path.join(output,".qcl-negf-frontend-v1.json")));
  }
  if (behavior === "unsafe") assert.equal(calls.length, 0, "runner called before safety refusal");
  else assert.ok(emptyBeforeCompiler);
}
console.log(JSON.stringify({error, calls:calls.length}));
'''


class GeneratorLifecycleTests(ReceiptFixtureCase):
    def node_run(self, mode, behavior, old_stamp="valid"):
        node = shutil.which("node")
        self.assertIsNotNone(node, "prepared Node 24 required; do not install during this test")
        area = Path(self.area.name) / f"{mode}-{behavior}"
        portal, frontend, output = fixture(area, mode, real_tool=True)
        receipt(frontend, output, mode)
        if old_stamp == "missing":
            (output / STAMP).unlink()
        elif old_stamp == "corrupt":
            put(output / STAMP, "not a receipt")
        foreign = area / "foreign"
        sentinel = foreign / "sentinel"
        put(sentinel, b"foreign sentinel\n")
        if behavior == "unsafe":
            (output / "assets/app.js").unlink()
            (output / "assets/app.js").symlink_to(sentinel)
        environment = {key: value for key, value in os.environ.items()
                       if key != "NODE_OPTIONS" and not key.startswith("VITE_")}
        result = subprocess.run(
            [node, "--input-type=module", "-e", NODE_HARNESS,
             str(frontend), str(output), str(foreign), behavior],
            env=environment, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"foreign sentinel\n")
        if behavior == "fresh":
            value = json.loads((output / STAMP).read_bytes())
            row = next(row for row in value["outputs"] if row["path"] == "index.html")
            self.assertEqual(row["sha256"], digest(b"<html>new</html>\n"))
        # Separate Portal hook fixture for Nix: copy selected dist only and restore original config.
        if mode == "nix-dist":
            wheel_portal, wheel_front, wheel_output = fixture(area / "wheel")
            shutil.rmtree(wheel_front)
            shutil.copytree(
                frontend, wheel_front, ignore=shutil.ignore_patterns("node_modules", "dist")
            )
            config = wheel_front / "vite.config.ts"
            put(config, config.read_bytes().replace(
                b'outDir: "dist"', b'outDir: "../src/qcl_negf_api/static"'
            ))
            shutil.rmtree(wheel_output)
            shutil.copytree(output, wheel_output, symlinks=True)
            portal = wheel_portal
        previous = self.portal
        self.portal = portal
        try:
            if behavior == "fresh":
                self.assertEqual(self.wheel(), {})
            elif behavior != "unsafe":
                with self.assertRaisesRegex(
                    RuntimeError, "QCL_FRONTEND_RECEIPT_INVALID:(missing|outputs);"
                ):
                    self.wheel()
        finally:
            self.portal = previous

    def test_generated_receipt_binds_new_payload_in_both_modes(self):
        for mode in ("source-static", "nix-dist"):
            with self.subTest(mode=mode):
                self.node_run(mode, "fresh")

    def test_old_payload_cannot_survive_zero_exit_without_selected_output(self):
        for mode in ("source-static", "nix-dist"):
            for behavior in ("no-output", "misdirected"):
                for old_stamp in ("valid", "missing", "corrupt"):
                    with self.subTest(mode=mode, behavior=behavior, old_stamp=old_stamp):
                        self.node_run(mode, behavior, old_stamp)

    def test_compiler_failure_or_source_mutation_never_publishes_receipt(self):
        for mode in ("source-static", "nix-dist"):
            for behavior in ("tsc-fail", "vite-fail", "mutate"):
                with self.subTest(mode=mode, behavior=behavior):
                    self.node_run(mode, behavior)

    def test_unsafe_selected_tree_is_refused_before_any_runner_call(self):
        for mode in ("source-static", "nix-dist"):
            with self.subTest(mode=mode):
                self.node_run(mode, "unsafe")


if __name__ == "__main__":
    unittest.main()
