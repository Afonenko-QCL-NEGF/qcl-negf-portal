"""Fail-closed runtime configuration; credentials never enter build artifacts."""

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_codes: tuple[str, ...]
    profile: str = "qcl-negf"
    scratch_root: str | None = None
    max_machines: int = 1
    max_processes_per_machine: int = 1
    max_cores_per_process: int = 64
    max_wallclock_seconds: int = 604800
    max_memory_kb: int = 134217728
    default_memory_kb: int = 4194304
    max_body_bytes: int = 2_000_000
    max_download_bytes: int = 200_000_000

    def __post_init__(self) -> None:
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
            )
            if "QCL_NEGF_" + name.upper() in os.environ
        }
        return cls(
            token=Path(token_file).read_text(encoding="ascii").strip(),
            allowed_codes=codes,
            profile=os.environ.get("QCL_NEGF_AIIDA_PROFILE", "qcl-negf"),
            scratch_root=os.environ.get("QCL_NEGF_SCRATCH_ROOT"),
            **limits,
        )
