"""Require compiled browser assets when producing a distributable package."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        static = Path(self.root) / "src" / "qcl_negf_api" / "static"
        if not (static / "index.html").is_file() or not list((static / "assets").glob("*.js")):
            raise RuntimeError("Build browser assets with npm --prefix frontend run build first")
