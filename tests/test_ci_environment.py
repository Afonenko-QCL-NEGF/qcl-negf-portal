"""Run the real scoped Deno task with a local npm substitute; no dependencies/network."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DENO = shutil.which("deno")
ROOT = Path(__file__).resolve().parents[1]
FORWARDED = (
    "PATH",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
BLOCKED = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "DYLD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "NODE_OPTIONS",
    "NIX_LD",
    "UNRELATED_SENTINEL",
)


class CiEnvironmentTests(unittest.TestCase):
    def run_task(self, fail_at=None):
        self.assertIsNotNone(DENO, "Deno is required for the command-contract fixture")
        with tempfile.TemporaryDirectory(prefix="qcl-portal-ci-env-") as area:
            root = Path(area)
            (root / "bin").mkdir()
            (root / "scripts").mkdir()
            (root / "frontend").mkdir()
            (root / "home").mkdir()
            shutil.copyfile(ROOT / "scripts/ci.ts", root / "scripts/ci.ts")
            shutil.copyfile(ROOT / "deno.json", root / "deno.json")
            logfile = root / "calls.jsonl"
            npm = root / "bin/npm"
            npm.write_text(
                f"#!{sys.executable}\n"
                + "import json,os,subprocess,sys\n"
                + f"log={str(logfile)!r}\n"
                + "node=subprocess.check_output(['qcl-fixture-node'],text=True).strip()\n"
                + "with open(log,'a') as out: out.write(json.dumps({'args':sys.argv[1:],"
                "'cwd':os.getcwd(),'env':dict(os.environ),'node':node})+'\\n')\n"
                + f"sys.exit(23 if sys.argv[1:]=={fail_at!r} else 0)\n"
            )
            node = root / "bin/qcl-fixture-node"
            node.write_text(f"#!{sys.executable}\nprint('fixture-node')\n")
            npm.chmod(0o755)
            node.chmod(0o755)
            environment = {
                "PATH": str(root / "bin") + os.pathsep + os.environ.get("PATH", ""),
                "HOME": str(root / "home"),
                "HTTP_PROXY": "http://proxy.example.invalid:1234",
                "HTTPS_PROXY": "http://proxy.example.invalid:1234",
                "NO_PROXY": "localhost,127.0.0.1",
                "http_proxy": "http://proxy.example.invalid:4321",
                "https_proxy": "http://proxy.example.invalid:4321",
                "no_proxy": "localhost,127.0.0.1,::1",
                "LD_LIBRARY_PATH": str(root / "absent-loader-directory"),
                "LD_PRELOAD": "",
                "DYLD_LIBRARY_PATH": str(root / "absent-dyld-directory"),
                "DYLD_INSERT_LIBRARIES": "",
                "NODE_OPTIONS": "--fixture-must-not-leak",
                "NIX_LD": "/fixture-must-not-leak",
                "UNRELATED_SENTINEL": "must-not-leak",
            }
            result = subprocess.run(
                [DENO, "task", "test:frontend"],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            calls = (
                [json.loads(line) for line in logfile.read_text().splitlines()]
                if logfile.exists()
                else []
            )
            for call in calls:
                self.assertEqual(call["cwd"], str(root))
                self.assertEqual(call["node"], "fixture-node")
                for key in FORWARDED:
                    self.assertEqual(call["env"].get(key), environment[key], key)
                for key in BLOCKED:
                    self.assertNotIn(key, call["env"], key)
            return result, calls

    def test_scoped_task_runs_three_npm_commands_with_safe_environment(self):
        result, calls = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call["args"] for call in calls],
            [
                ["--prefix", "frontend", "ci"],
                ["--prefix", "frontend", "test"],
                ["--prefix", "frontend", "run", "build"],
            ],
        )

    def test_failed_npm_stops_before_build_and_reports_exit_status(self):
        result, calls = self.run_task(["--prefix", "frontend", "test"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("npm failed with exit status 23", result.stderr)
        self.assertEqual(
            [call["args"] for call in calls],
            [
                ["--prefix", "frontend", "ci"],
                ["--prefix", "frontend", "test"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
