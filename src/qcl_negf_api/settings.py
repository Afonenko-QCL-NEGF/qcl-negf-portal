"""Fail-closed runtime configuration; credentials never enter build artifacts."""

import math
import os
import re
from dataclasses import dataclass
from numbers import Real
from pathlib import Path, PurePosixPath
from uuid import UUID

ACTOR_DEFAULTS = {
    "actor_initialize_timeout_seconds": 30.0,
    "actor_call_timeout_seconds": 30.0,
    "actor_io_timeout_seconds": 15.0,
    "actor_close_timeout_seconds": 10.0,
    "actor_max_pending_calls": 16,
    "actor_max_open_artifacts": 8,
}


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_codes: tuple[str, ...]
    profile: str = "qcl-negf"
    scratch_root: str | None = None
    docs_bundle_dir: str | None = None
    docs_bundle_sha256: str | None = None
    configurator_executable: str | None = None
    configurator_catalog_root: str | None = None
    configurator_catalog_source_id: str | None = None
    configurator_cgroup_root: str | None = None
    saved_results_enabled: bool = True
    saved_results_binding_file: str | None = None
    max_machines: int = 1
    max_processes_per_machine: int = 1
    max_cores_per_process: int = 64
    max_wallclock_seconds: int = 604800
    max_memory_kb: int = 134217728
    default_memory_kb: int = 4194304
    max_body_bytes: int = 2_000_000
    max_download_bytes: int = 200_000_000
    export_disk_bytes: int = 100 * 1024**3
    export_ttl_seconds: int = 86400

    actor_initialize_timeout_seconds: float = ACTOR_DEFAULTS["actor_initialize_timeout_seconds"]
    actor_call_timeout_seconds: float = ACTOR_DEFAULTS["actor_call_timeout_seconds"]
    actor_io_timeout_seconds: float = ACTOR_DEFAULTS["actor_io_timeout_seconds"]
    actor_close_timeout_seconds: float = ACTOR_DEFAULTS["actor_close_timeout_seconds"]
    actor_max_pending_calls: int = ACTOR_DEFAULTS["actor_max_pending_calls"]
    actor_max_open_artifacts: int = ACTOR_DEFAULTS["actor_max_open_artifacts"]

    def __post_init__(self) -> None:
        if type(self.saved_results_enabled) is not bool:
            raise ValueError("saved_results_enabled must be boolean")
        for name in ACTOR_DEFAULTS:
            value = getattr(self, name)
            maximum = 256 if name == "actor_max_pending_calls" else 64
            if name.endswith("_seconds"):
                valid = (not isinstance(value, bool) and isinstance(value, Real)
                         and math.isfinite(value) and 0 < value <= 300)
            else:
                valid = type(value) is int and 1 <= value <= maximum
            if not valid:
                raise ValueError(f"Invalid {name}")
        if len(self.token) < 32 or not self.token.isascii():
            raise ValueError("API token must contain at least 32 ASCII characters")
        if not self.allowed_codes:
            raise ValueError("At least one approved AiiDA Code UUID is required")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.profile):
            raise ValueError("AiiDA profile must use letters, digits, dots, underscores or hyphens")
        if self.scratch_root is not None:
            scratch = PurePosixPath(self.scratch_root)
            if (
                not scratch.is_absolute()
                or ".." in scratch.parts
                or "\x00" in self.scratch_root
                or str(scratch) != self.scratch_root
            ):
                raise ValueError("Scratch root must be an absolute normalized POSIX path")
        if self.max_machines != 1 or self.max_processes_per_machine != 1:
            raise ValueError("The solver supports one machine and one process per execution")
        for code in self.allowed_codes:
            if str(UUID(code)) != code:
                raise ValueError("Approved code identifiers must be canonical UUIDs")
        if not 0 < self.default_memory_kb <= self.max_memory_kb:
            raise ValueError("Default memory must be positive and within the configured ceiling")
        if self.max_wallclock_seconds < 60:
            raise ValueError("Wall time ceiling must be at least 60 seconds")
        for name in (
            "max_machines",
            "max_processes_per_machine",
            "max_cores_per_process",
            "max_wallclock_seconds",
            "max_memory_kb",
            "default_memory_kb",
            "max_body_bytes",
            "max_download_bytes",
            "export_disk_bytes",
            "export_ttl_seconds",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_environment(cls) -> Settings:
        token_file = os.environ.get("QCL_NEGF_API_TOKEN_FILE")
        if not token_file:
            raise ValueError("QCL_NEGF_API_TOKEN_FILE is required")
        codes = tuple(
            item.strip()
            for item in os.environ.get("QCL_NEGF_ALLOWED_CODES", "").split(",")
            if item.strip()
        )
        limits = {
            name: int(os.environ["QCL_NEGF_" + name.upper()])
            for name in (
                "max_machines",
                "max_processes_per_machine",
                "max_cores_per_process",
                "max_wallclock_seconds",
                "max_memory_kb",
                "default_memory_kb",
                "max_body_bytes",
                "max_download_bytes",
                "export_disk_bytes",
                "export_ttl_seconds",
            )
            if "QCL_NEGF_" + name.upper() in os.environ
        }
        for name in ACTOR_DEFAULTS:
            raw = os.environ.get("QCL_NEGF_" + name.upper())
            if raw is not None:
                if name.endswith("_seconds"):
                    limits[name] = float(raw)
                else:
                    if not raw.isdecimal():
                        raise ValueError(f"Invalid {name}")
                    limits[name] = int(raw)
        return cls(
            token=Path(token_file).read_text(encoding="ascii").strip(),
            allowed_codes=codes,
            profile=os.environ.get("QCL_NEGF_AIIDA_PROFILE", "qcl-negf"),
            scratch_root=os.environ.get("QCL_NEGF_SCRATCH_ROOT"),
            docs_bundle_dir=os.environ.get("QCL_NEGF_DOCS_BUNDLE_DIR"),
            docs_bundle_sha256=os.environ.get("QCL_NEGF_DOCS_BUNDLE_SHA256"),
            configurator_executable=os.environ.get("QCL_NEGF_CONFIGURATOR_EXECUTABLE"),
            configurator_catalog_root=os.environ.get("QCL_NEGF_CONFIGURATOR_CATALOG_ROOT"),
            configurator_catalog_source_id=os.environ.get("QCL_NEGF_CONFIGURATOR_CATALOG_SOURCE_ID"),
            configurator_cgroup_root=os.environ.get("QCL_NEGF_CONFIGURATOR_CGROUP_ROOT"),
            saved_results_binding_file=os.environ.get("QCL_NEGF_SAVED_RESULTS_BINDING_FILE"),
            saved_results_enabled=(
                os.environ.get("QCL_NEGF_SAVED_RESULTS_ENABLED", "true") == "true"
            ),
            **limits,
        )
