"""Independently bind release browser bytes to their full frontend source."""

import hashlib
import json
import re
import stat
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

STAMP = ".qcl-negf-frontend-v1.json"
SCRIPT = "node tools/asset-receipt.mjs build"
COMMANDS = [
    ["node", "node_modules/typescript/bin/tsc", "--noEmit"],
    ["node", "node_modules/vite/bin/vite.js", "build", "--mode", "production"],
]
FIELDS = ["format", "mode", "recipe", "recipe_sha256", "toolchain", "inputs",
          "inputs_sha256", "outputs", "outputs_sha256"]
REQUIRED = {"package.json", "package-lock.json", "index.html", "tsconfig.json",
            "vite.config.ts", "tools/asset-receipt.mjs", "src/main.tsx"}
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


def _fail(reason):
    raise RuntimeError(
        f"QCL_FRONTEND_RECEIPT_INVALID:{reason}; "
        "run npm --prefix frontend run build with prepared locked dependencies"
    )


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("schema")
        value[key] = item
    return value


def _constant(_value):
    _fail("schema")


def _json(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        _fail("schema")


def _keys(value, keys):
    if type(value) is not dict or list(value) != keys:
        _fail("schema")


def _path(value):
    if (type(value) is not str or not value or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        _fail("schema")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail("schema")


def _hash(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _fail("schema")


def _rows(rows):
    if type(rows) is not list or not rows:
        _fail("schema")
    previous = None
    for row in rows:
        _keys(row, ["path", "bytes", "sha256"])
        _path(row["path"])
        if type(row["bytes"]) is not int or not 0 <= row["bytes"] <= 9007199254740991:
            _fail("schema")
        _hash(row["sha256"])
        encoded = row["path"].encode("utf-8")
        if previous is not None and encoded <= previous:
            _fail("schema")
        previous = encoded


def _lstat(path, device=None, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or (device is not None and info.st_dev != device)
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode))):
        _fail("unsafe-path")
    return info


def _directory(path, device=None):
    return _lstat(path, device, directory=True)


def _inventory(root, *, inputs=False, transform=False):
    device = _directory(root).st_dev
    rows = []

    def walk(directory, prefix=""):
        for entry in directory.iterdir():
            name = entry.name
            relative = prefix + name
            try:
                name.encode("utf-8")
            except UnicodeError:
                _fail("unsafe-path")
            _path(relative)
            if name == ".git":
                _fail("unsafe-path")
            info = _lstat(entry, device)
            if (inputs and not prefix and name in ("node_modules", "dist")
                    and stat.S_ISDIR(info.st_mode)):
                continue
            if inputs and (name == ".env" or name.startswith(".env.")):
                _fail("unsafe-path")
            if stat.S_ISDIR(info.st_mode):
                walk(entry, relative + "/")
            elif not (not inputs and relative == STAMP):
                data = entry.read_bytes()
                if inputs and transform and relative == "vite.config.ts":
                    token = b"../src/qcl_negf_api/static"
                    if data.count(token) != 1:
                        _fail("mode")
                    data = data.replace(token, b"dist")
                rows.append({"path": relative, "bytes": len(data), "sha256": _sha(data)})

    walk(root)
    return sorted(rows, key=lambda row: row["path"].encode("utf-8"))


def _recipe(mode):
    return {"id": "qcl-negf.frontend-build.v1", "commands": COMMANDS,
            "output_root": "../src/qcl_negf_api/static" if mode == "source-static" else "dist",
            "node_env": "production", "output_reset": "clean-selected-root-v1"}


def _verify(portal):
    device = _directory(portal).st_dev
    frontend = portal / "frontend"
    output = portal / "src/qcl_negf_api/static"
    try:
        _directory(frontend, device)
        for directory in (portal / "src", portal / "src/qcl_negf_api", output):
            _directory(directory, device)
        if not stat.S_ISREG(_lstat(output / STAMP, device).st_mode):
            _fail("unsafe-path")
        raw = (output / STAMP).read_bytes()
    except FileNotFoundError:
        _fail("missing")
    value = _json(raw)
    _keys(value, FIELDS)
    try:
        if raw != _canonical(value) + b"\n":
            _fail("schema")
    except (UnicodeError, ValueError):
        _fail("schema")
    if value["format"] != "qcl-negf.frontend-receipt.v1":
        _fail("schema")
    mode = value["mode"]
    if mode not in ("source-static", "nix-dist"):
        _fail("mode")
    _keys(value["recipe"], ["id", "commands", "output_root", "node_env", "output_reset"])
    if value["recipe"] != _recipe(mode):
        _fail("recipe")
    for field in ("recipe", "inputs", "outputs"):
        _hash(value[field + "_sha256"])
    _rows(value["inputs"])
    _rows(value["outputs"])
    _keys(value["toolchain"], ["node", "npm", "typescript", "vite"])
    for version in value["toolchain"].values():
        if type(version) is not str or SEMVER.fullmatch(version) is None:
            _fail("toolchain")
    if re.fullmatch(r"24\.[0-9]+\.[0-9]+", value["toolchain"]["node"]) is None:
        _fail("toolchain")
    for field in ("recipe", "inputs", "outputs"):
        if _sha(_canonical(value[field])) != value[field + "_sha256"]:
            _fail(field)
    actual_inputs = _inventory(frontend, inputs=True, transform=mode == "nix-dist")
    if (actual_inputs != value["inputs"]
            or not REQUIRED.issubset(row["path"] for row in actual_inputs)):
        _fail("inputs")
    config = (frontend / "vite.config.ts").read_bytes()
    if config.count(b"../src/qcl_negf_api/static") != 1:
        _fail("mode")
    package = _json((frontend / "package.json").read_bytes())
    if (type(package) is not dict or package.get("name") != "qcl-negf-portal"
            or package.get("private") is not True or type(package.get("scripts")) is not dict
            or package["scripts"].get("build") != SCRIPT
            or "prebuild" in package["scripts"] or "postbuild" in package["scripts"]):
        _fail("recipe")
    lock = _json((frontend / "package-lock.json").read_bytes())
    try:
        for name in ("typescript", "vite"):
            if value["toolchain"][name] != lock["packages"]["node_modules/" + name]["version"]:
                _fail("toolchain")
    except (KeyError, TypeError):
        _fail("toolchain")
    actual_outputs = _inventory(output)
    if actual_outputs != value["outputs"]:
        _fail("outputs")
    if not any(row["path"] == "index.html" and row["bytes"] > 0 for row in actual_outputs):
        _fail("outputs")
    if not any(row["path"].startswith("assets/") and row["path"].count("/") == 1
               and row["path"].endswith(".js") and row["bytes"] > 0 for row in actual_outputs):
        _fail("outputs")


def verify_frontend_assets(portal_root: Path) -> None:
    """Read source/static only; no Node, package imports, network or rebuild."""
    try:
        _verify(Path(portal_root))
    except OSError:
        _fail("io")
    except (ValueError, UnicodeError, RecursionError):
        _fail("schema")


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        verify_frontend_assets(Path(self.root))
