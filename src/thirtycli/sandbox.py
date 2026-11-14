"""Sandbox abstraction that tells the truth about what it enforces.

There is no OS isolation here. Unless `bubblewrap` is found on a POSIX box, the
report says `available=False` and a run is a plain subprocess with a pinned cwd,
a scrubbed environment and a timeout. Filesystem access, network access and the
process tree are **not** confined, and the report says so in plain words.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SandboxReport:
    available: bool
    kind: str
    reason: str
    filesystem: str
    network: str
    process: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


# Environment names the child process genuinely needs. Everything else goes,
# which is how API keys stop leaking into shell commands.
_KEEP_ENV = {
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "TZ",
    "TERM",
}

_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")


def capability_report() -> SandboxReport:
    """Describe what isolation actually exists on this machine. Never guesses."""
    if sys.platform != "win32":
        bwrap = shutil.which("bwrap")
        if bwrap:
            return SandboxReport(
                available=True,
                kind="bwrap",
                reason=f"bubblewrap found at {bwrap}",
                filesystem="namespace (bwrap); not used by ProcessSandbox yet",
                network="network namespace available; not used by ProcessSandbox yet",
                process="pid namespace available; not used by ProcessSandbox yet",
            )
    return SandboxReport(
        available=False,
        kind="process",
        reason="no OS isolation detected (bwrap/landlock/seatbelt unavailable); cwd+env+timeout only",
        filesystem="workspace cwd only (not enforced)",
        network="not confined",
        process="timeout only (no pid or job isolation)",
    )


def restricted_env(extra: dict | None = None) -> dict:
    """Child environment: allowlisted names only, credentials removed by name."""
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        if upper in _KEEP_ENV or upper.startswith("LC_"):
            env[name] = value
    env["THIRTYCLI_SANDBOX"] = "1"
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass
class ProcessSandbox:
    """Run shell commands with a pinned cwd, a scrubbed env and a timeout.

    This is not a jail: the command can read the whole disk and use the network
    if `allow_network` is True. `allow_network` is reported, not enforced; the
    permission layer is what refuses network-hungry commands.
    """

    root: Path
    timeout: int = 120
    allow_network: bool = False
    report: SandboxReport = field(default_factory=capability_report, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    def run(self, command: str, timeout: int | None = None) -> CommandResult:
        """Run `command` through the shell in `root`. Never raises for bad commands."""
        text = (command or "").strip()
        if not text:
            return CommandResult(2, "", "empty command: nothing to run", False)
        limit = self.timeout if timeout is None else int(timeout)
        if limit <= 0:
            limit = self.timeout
        try:
            proc = subprocess.run(
                text,
                shell=True,
                cwd=str(self.root),
                env=restricted_env(),
                capture_output=True,
                text=True,
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                -1,
                _as_text(exc.stdout),
                _as_text(exc.stderr) or f"timed out after {limit}s",
                True,
            )
        except OSError as exc:
            return CommandResult(2, "", f"could not run command: {exc}", False)
        return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")

    def describe(self) -> dict:
        """Report + the knobs in force, so callers can print what is real."""
        report = self.report
        return {
            "root": str(self.root),
            "timeout": self.timeout,
            "allow_network": self.allow_network,
            "sandbox": {
                "available": report.available,
                "kind": report.kind,
                "reason": report.reason,
                "filesystem": report.filesystem,
                "network": report.network,
                "process": report.process,
            },
        }
