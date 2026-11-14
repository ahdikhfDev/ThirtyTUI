"""Permission levels for sub-agent tools, enforced in one honest place.

`check()` is a decision, not an action: it resolves path strings, pattern-matches
the shell command and returns a verdict. It never reads files, never runs a
command and never prompts. `authorize()` is `check()` plus at most one approver
call, and it fails closed: no approver means no permission, and an approver that
raises is treated as a refusal.

The policy deliberately does no OS isolation. Shell commands that reach
`authorize()` still run unsandboxed; `sandbox.py` says so in its report, and an
autonomous shell needs approval unless the caller explicitly accepted that.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .sandbox import capability_report


class PermissionLevel(str, Enum):  # noqa: UP042 - keep the documented API
    READ_ONLY = "read-only"
    CONTROLLED = "controlled"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    approval_required: bool = False
    reason: str = ""


READ_ONLY_TOOLS = {
    "read_file",
    "list_dir",
    "search_text",
    "search_files",
    "web_fetch",
    "web_search",
    "git_status",
    "git_diff",
    "git_log",
    "git_branch",
    "detect_framework",
    "detect_language",
    "inspect_dependencies",
    "inspect_project_structure",
    "now",
}

WRITE_TOOLS = {"write_file", "edit_file", "delete_file", "git_commit"}

SHELL_TOOLS = {"run_shell"}

# Project runners execute repository code, so they follow the shell policy rather
# than being treated as harmless reads.
QUALITY_TOOLS = {"run_tests", "run_linter", "run_formatter", "run_build"}

NETWORK_TOOLS = {"web_fetch", "web_search"}

PATH_ARG_TOOLS = {"read_file", "list_dir", "write_file", "edit_file", "delete_file"}

GLOB_ARG_TOOLS = {"search_text", "search_files"}


# --------------------------------------------------------------------------- paths


def resolve_target(root: Path, path: str) -> tuple[Path, bool]:
    """Resolve a tool path against the workspace. Returns (path, inside_workspace)."""
    base = Path(root).resolve()
    raw = (path or "").strip()
    if not raw:
        return base, True
    candidate = Path(raw).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    return target, _within(base, target)


def _within(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        pass
    # Windows paths are case-insensitive; Path comparison is not. Guard the prefix
    # with a separator so `C:\\work-evil` never counts as inside `C:\\work`.
    left = os.path.normcase(str(base)).rstrip("\\/")
    right = os.path.normcase(str(target))
    return right == left or right.startswith(left + os.sep)


# ----------------------------------------------------------------------- commands

_WHITESPACE = re.compile(r"\s+")
_SEGMENT = re.compile(r"\|\||&&|[|;\n]")
_ROOTISH = re.compile(
    r"^(?:/|/\*|~|~/|\$HOME|\$\{HOME\}|%USERPROFILE%\*?|[a-zA-Z]:[\\/]?\*?|\.\.?|\./|(?:\./)?\*)$",
    re.IGNORECASE,
)

_DELETE_HEADS = {"rm", "del", "erase", "rd", "rmdir", "remove-item", "ri"}
_ESCALATION_HEADS = {"sudo", "su", "doas", "runas", "pkexec"}
_POWER_HEADS = {"shutdown", "reboot", "halt", "poweroff", "diskpart", "format", "format.com"}
_NETWORK_HEADS = {
    "curl",
    "wget",
    "iwr",
    "irm",
    "invoke-webrequest",
    "invoke-restmethod",
    "aria2c",
}

_DANGEROUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bmkfs(?:\.\w+)?\b"),
        "formatting a filesystem (mkfs)",
    ),
    (
        re.compile(r"\bdd\b[^|;&]*\bif\s*="),
        "raw disk read/write (dd if=)",
    ),
    (
        re.compile(r"\bgit\b[^|;&]*\breset\b[^|;&]*--hard\b"),
        "discarding uncommitted work (git reset --hard)",
    ),
    (
        re.compile(r"\bgit\b[^|;&]*\bclean\b[^|;&]*(?:\s-[a-zA-Z]*f)"),
        "deleting untracked files (git clean -f)",
    ),
    (
        re.compile(r"\bgit\b[^|;&]*\bpush\b[^|;&]*(?:--force\b|--force-with-lease\b|\s-f\b)"),
        "rewriting remote history (git push --force)",
    ),
    (
        re.compile(
            r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*(?:sudo\s+)?"
            r"(?:sh|bash|zsh|dash|ksh|python|python3|perl|ruby|node|powershell|pwsh|cmd|iex)\b"
        ),
        "piping a download straight into a shell",
    ),
    (
        re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^|;&]*\s-(?:enc|encodedcommand|encoded)\b"),
        "obfuscated PowerShell (-enc)",
    ),
    (
        re.compile(r"\b(?:invoke-expression|iex)\b"),
        "executing a string as code (Invoke-Expression)",
    ),
    (
        re.compile(r"\b(?:mimikatz|secretsdump|hashdump)\b"),
        "credential dumping",
    ),
    (
        re.compile(r"\breg\s+save\s+hklm\b"),
        "dumping local password hashes (reg save hklm)",
    ),
    (
        re.compile(
            r"(?:\.ssh[\\/]id_[a-z0-9]+|\bid_rsa\b|\bid_ed25519\b|\.aws[\\/]credentials"
            r"|\.netrc\b|\.git-credentials\b|\.pgpass\b)"
        ),
        "reading private keys or stored credentials",
    ),
)


def _basename(token: str) -> str:
    return token.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _tokens(command: str) -> list[str]:
    text = _WHITESPACE.sub(" ", (command or "").strip())
    if not text:
        return []
    try:
        # posix=False keeps Windows backslashes and `$HOME` intact.
        parts = shlex.split(text, posix=False)
    except ValueError:
        parts = text.split()
    return [part.strip("\"'") for part in parts if part.strip("\"'")]


def _segments(command: str) -> list[str]:
    return [part.strip() for part in _SEGMENT.split(_WHITESPACE.sub(" ", command)) if part.strip()]


def _segment_reason(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    head = _basename(tokens[0])
    lowered = [token.lower() for token in tokens]
    flags = {token.split("=", 1)[0].lower() for token in lowered}

    if head in _ESCALATION_HEADS:
        return f"privilege escalation ({head})"
    if head in _POWER_HEADS or head.startswith("mkfs"):
        return f"system-level destructive command ({head})"
    if head == "dd" and any(flag.startswith("if=") or flag.startswith("of=") for flag in lowered[1:]):
        return "raw disk read/write (dd if=)"
    if head == "reg" and "save" in lowered and any("hklm" in token for token in lowered):
        return "dumping local password hashes (reg save hklm)"
    if head in {"powershell", "pwsh", "powershell.exe", "pwsh.exe"} and flags & {
        "-enc",
        "-encodedcommand",
        "/enc",
    }:
        return "obfuscated PowerShell (-enc)"
    if head in {"invoke-expression", "iex"}:
        return "executing a string as code (Invoke-Expression)"
    if head in _DELETE_HEADS:
        targets = [token for token in tokens[1:] if token and not token.startswith("-")]
        if any(_ROOTISH.match(token) for token in targets):
            return "recursive delete of a filesystem root, home directory or whole workspace"
    return None


def dangerous_reason(command: str) -> str | None:
    """Why this shell command must never run, or None when it looks ordinary."""
    text = _WHITESPACE.sub(" ", (command or "").strip())
    if not text:
        return None
    haystack = text.lower()
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.search(haystack):
            return reason
    for segment in _segments(text):
        reason = _segment_reason(_tokens(segment))
        if reason:
            return reason
    return None


def _network_reason(command: str) -> str | None:
    """Why this command needs the network, or None when it does not."""
    for segment in _segments(command):
        tokens = _tokens(segment)
        if not tokens:
            continue
        head = _basename(tokens[0])
        lowered = [token.lower() for token in tokens]
        if head in _NETWORK_HEADS:
            return f"network access is disabled: {head} needs the network"
        if head in {"pip", "pip3", "pipx", "npm", "pnpm", "yarn", "bun"} and "install" in lowered:
            return f"network access is disabled: {head} install downloads packages"
        if head in {"poetry", "uv", "pipenv"} and {"add", "install", "sync"} & set(lowered):
            return f"network access is disabled: {head} resolves packages over the network"
        if head in {"python", "python3", "py"} and "pip" in lowered and "install" in lowered:
            return "network access is disabled: pip install downloads packages"
    return None


def check_command(command: str) -> PermissionDecision:
    """Stateless command verdict: dangerous commands are refused at every level."""
    reason = dangerous_reason(command)
    if reason:
        return PermissionDecision(False, False, f"dangerous command blocked: {reason}")
    if not (command or "").strip():
        return PermissionDecision(False, False, "empty command")
    return PermissionDecision(True)


# ------------------------------------------------------------------------ policy


def _deny(reason: str) -> PermissionDecision:
    return PermissionDecision(False, False, reason)


def _approval(reason: str) -> PermissionDecision:
    return PermissionDecision(True, True, reason)


@dataclass
class PermissionPolicy:
    """Decide what a worker may do, given its level and its workspace.

    `allowed=True` with `approval_required=True` means "permitted once somebody
    approves"; call `authorize()` to resolve that. Passing `sandbox_available`
    overrides the probe, which keeps `check()` free of process or filesystem
    inspection.
    """

    root: Path
    level: PermissionLevel = PermissionLevel.CONTROLLED
    approver: Callable[[str], bool] | None = None
    allow_network: bool = True
    unsandboxed: bool = False
    sandbox_available: bool | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if not isinstance(self.level, PermissionLevel):
            self.level = PermissionLevel(self.level)
        if self.sandbox_available is None:
            self.sandbox_available = capability_report().available

    # -- checks ------------------------------------------------------------

    def check(self, tool: str, args: dict) -> PermissionDecision:
        """Pure verdict for one call. Never prompts and never executes anything."""
        name = (tool or "").strip()
        options = args or {}

        path_decision = self._check_path(name, options)
        if path_decision is not None:
            return path_decision

        if name in READ_ONLY_TOOLS:
            if name in NETWORK_TOOLS and not self.allow_network:
                return _deny(f"network access is disabled: {name} needs the network")
            return PermissionDecision(True)

        if name in SHELL_TOOLS:
            return self._check_shell(str(options.get("command", "") or ""))

        if name in QUALITY_TOOLS:
            return self._check_quality(name)

        if name in WRITE_TOOLS:
            return self._check_write(name, options)

        return _deny(f"unknown tool: {name or '(empty)'}")

    def authorize(self, tool: str, args: dict) -> PermissionDecision:
        """`check()`, then ask the approver once if the call needs approval."""
        decision = self.check(tool, args)
        if not decision.allowed or not decision.approval_required:
            return decision
        if self.approver is None:
            return _deny("approval required but no approver is configured (failing closed)")
        try:
            granted = bool(self.approver(self._question(tool, args)))
        except Exception:  # an approver that crashes must not grant access
            return _deny("approver raised an error; failing closed")
        if not granted:
            return _deny("denied by user")
        return PermissionDecision(True, False, decision.reason)

    # -- internals ---------------------------------------------------------

    def _check_path(self, name: str, options: dict) -> PermissionDecision | None:
        if name in PATH_ARG_TOOLS:
            raw = str(options.get("path", "") or "")
        elif name in GLOB_ARG_TOOLS:
            raw = str(options.get("glob" if name == "search_text" else "pattern", "") or "")
            if raw and (Path(raw).is_absolute() or ".." in Path(raw).parts):
                return _deny(f"outside workspace: {raw}")
            return None
        else:
            return None
        target, inside = resolve_target(self.root, raw)
        if not inside:
            return _deny(f"outside workspace: {raw} resolves to {target}")
        return None

    def _check_shell(self, command: str) -> PermissionDecision:
        reason = dangerous_reason(command)
        if reason:
            return _deny(f"dangerous command blocked: {reason}")
        if not command.strip():
            return _deny("empty command")
        if not self.allow_network:
            network = _network_reason(command)
            if network:
                return _deny(network)
        if self.level is PermissionLevel.READ_ONLY:
            return _deny("read-only level: shell commands are disabled")
        if self.level is PermissionLevel.CONTROLLED:
            return _approval(f"controlled level: shell command needs approval: {command}")
        if self.unsandboxed or self.sandbox_available:
            return PermissionDecision(True)
        return _approval(f"no OS sandbox is available; approval needed to run unsandboxed: {command}")

    def _check_write(self, name: str, options: dict) -> PermissionDecision:
        if self.level is PermissionLevel.READ_ONLY:
            return _deny(f"read-only level: {name} is disabled")
        if self.level is PermissionLevel.CONTROLLED:
            return _approval(f"controlled level: {name} needs approval")
        return PermissionDecision(True)

    def _check_quality(self, name: str) -> PermissionDecision:
        if self.level is PermissionLevel.READ_ONLY:
            return _deny(f"read-only level: {name} would execute project code")
        if self.level is PermissionLevel.CONTROLLED:
            return _approval(f"controlled level: {name} runs project commands and needs approval")
        if self.unsandboxed or self.sandbox_available:
            return PermissionDecision(True)
        return _approval(f"no OS sandbox is available; approval needed to run {name} unsandboxed")

    def _question(self, tool: str, args: dict) -> str:
        options = args or {}
        if tool in SHELL_TOOLS:
            return f"Run shell command in {self.root}: {options.get('command', '')}"
        path = options.get("path")
        if path:
            return f"Allow {tool} on {path} in {self.root}?"
        return f"Allow {tool} in {self.root}?"
