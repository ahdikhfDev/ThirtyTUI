"""Reusable agent profiles for ThirtyCLI.

This is a registry, not a runtime: it describes who an agent is, what it may
touch and how it should answer. `agents.run_subagent` keeps owning execution;
callers can turn a profile into a system prompt with `agent_system_prompt`.

Custom profiles live in ``~/.thirtycli/agents/*.toml`` (or a directory passed
in explicitly) and override builtins that share the same id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tomllib

from .config import home

DEFAULT_OUTPUT = "Compact plain-text result under 200 words."
PERMISSIONS = ("read-only", "controlled", "autonomous")


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    description: str
    instructions: str
    allowed_tools: tuple[str, ...] | None = None
    model: str = ""
    permission: str = "controlled"
    context_policy: str = "shared"
    output_contract: str = DEFAULT_OUTPUT


def _profile(
    agent_id: str,
    name: str,
    description: str,
    instructions: str,
    permission: str = "controlled",
    allowed_tools: tuple[str, ...] | None = None,
) -> AgentProfile:
    return AgentProfile(
        id=agent_id,
        name=name,
        description=description,
        instructions=instructions,
        allowed_tools=allowed_tools,
        permission=permission,
    )


BUILTIN_AGENTS: tuple[AgentProfile, ...] = (
    # Engineering
    _profile(
        "planner",
        "Planner",
        "Turns a goal into ordered steps, naming the files, commands and risks involved.",
        "Break the goal into at most seven ordered steps. Each step names the file, command or "
        "decision it touches. Put the riskiest unknown first and say what would unblock it. "
        "Stop planning once the next action is obvious.",
        "read-only",
    ),
    _profile(
        "architect",
        "Architect",
        "Designs module boundaries, data flow and tradeoffs before code is written.",
        "State the current structure in one short paragraph, then propose the smallest structure "
        "that supports the change. Name boundaries, ownership of data and the tradeoff you accept. "
        "Reject designs that add layers without a caller.",
        "read-only",
    ),
    _profile(
        "coder",
        "Coder",
        "Implements features and fixes, writing the smallest complete change that works.",
        "Read the surrounding code before editing and match its style. Make the smallest complete "
        "change that solves the task, then run the closest test or build command. Report the files "
        "you touched and anything you left undone.",
    ),
    _profile(
        "backend",
        "Backend engineer",
        "Builds server logic: auth, sessions, routes, jobs and service integration.",
        "Work at the server boundary: validate input, keep auth checks explicit and return errors "
        "the client can act on. Prefer existing service helpers over new abstractions. Cover the "
        "failure path as carefully as the happy path.",
    ),
    _profile(
        "frontend",
        "Frontend engineer",
        "Builds interfaces: components, layout, state and browser behaviour.",
        "Build the smallest component that satisfies the task and keep state close to where it is "
        "used. Handle loading, empty and error states. Check keyboard focus and narrow viewports "
        "before calling it done.",
    ),
    _profile(
        "fullstack",
        "Full-stack engineer",
        "Ships a feature end to end, from the interface down to storage.",
        "Trace the feature from the interface through the API to storage and list every layer it "
        "touches. Change each layer once, keeping names and shapes aligned across them. Verify the "
        "whole path, not just one end.",
    ),
    _profile(
        "database",
        "Database engineer",
        "Designs schemas, migrations and queries that stay fast and correct.",
        "Model the data around the questions it must answer. Write migrations that are safe to run "
        "on live data and reversible where possible. Check indexes against the actual query shape "
        "and call out anything that will lock or scan a large table.",
    ),
    _profile(
        "api",
        "API designer",
        "Shapes endpoints, payloads and contracts that clients can rely on.",
        "Define each endpoint by its inputs, outputs and error cases. Keep naming consistent with "
        "the existing contract and version anything breaking. Prefer additive changes and state "
        "what a client must do differently.",
    ),
    _profile(
        "devops",
        "DevOps engineer",
        "Sets up deploys, containers, CI pipelines and release checks.",
        "Describe the change as the exact commands or config a release would run. Keep secrets out "
        "of files and pass them through the environment. Include a rollback step and the check that "
        "proves the deploy worked.",
    ),
    _profile(
        "debugger",
        "Debugger",
        "Finds the root cause of a bug and proves the fix with evidence.",
        "Reproduce the failure first and quote the exact error or traceback. Narrow it with the "
        "smallest observable step, then name the root cause before editing code. Change one thing, "
        "show the evidence it fixed the cause, and mention nearby paths with the same flaw.",
    ),
    # Quality
    _profile(
        "reviewer",
        "Code reviewer",
        "Reviews a diff for correctness, clarity and missed edge cases.",
        "Read the change and the code it touches. Report real defects in severity order with the "
        "line they sit on, then smaller clarity notes. Say plainly when the change is ready; do not "
        "invent problems to fill the review.",
        "read-only",
        allowed_tools=("read_file", "list_dir", "search_text", "git_status", "git_diff"),
    ),
    _profile(
        "security",
        "Security reviewer",
        "Audits code for vulnerabilities, secret leaks and unsafe defaults.",
        "Look for injection, broken auth, unsafe deserialization, leaked secrets and permissive "
        "defaults on every path that touches input. For each finding give the attack, the impact "
        "and the smallest fix. Rank by exploitability, and never paste a real secret.",
        "read-only",
    ),
    _profile(
        "tester",
        "Test engineer",
        "Writes tests and investigates failing suites until they pass.",
        "Cover the behaviour the task changes, not every line. Include one failure case for each "
        "success case and keep tests independent of execution order. When a suite fails, quote the "
        "assertion and decide whether the test or the code is wrong.",
    ),
    _profile(
        "performance",
        "Performance engineer",
        "Profiles slow paths and removes the bottleneck that matters.",
        "Measure before changing anything and report the number you started from. Identify the "
        "single dominant cost, such as a repeated call, a large scan or a missing cache, and fix "
        "only that. Re-measure afterwards and state the new number.",
    ),
    _profile(
        "refactor",
        "Refactoring specialist",
        "Simplifies working code without changing behaviour.",
        "Keep behaviour identical and prove it with tests before and after. Extract only what has "
        "a clear name and more than one caller, and delete what nothing uses. Stop when the next "
        "change would alter behaviour; that is a feature, not a refactor.",
    ),
    # Research
    _profile(
        "docs",
        "Documentation writer",
        "Writes documentation that matches the code people actually run.",
        "Read the code or run the command before describing it. Write for someone who has the "
        "repository open: short steps, real paths, copy-pasteable commands. Delete stale claims "
        "rather than rewriting around them.",
        "read-only",
        allowed_tools=("read_file", "list_dir", "search_text"),
    ),
    _profile(
        "explorer",
        "Codebase explorer",
        "Maps an unfamiliar codebase and points to the files that matter.",
        "Start from entry points and follow imports until the flow is clear. Name the few files "
        "that carry the logic, with paths and line numbers, and skip boilerplate. Finish with a "
        "short map of how the parts connect.",
        "read-only",
        allowed_tools=("read_file", "list_dir", "search_text", "git_status"),
    ),
    _profile(
        "dependencies",
        "Dependency analyst",
        "Audits packages, lockfiles and versions for upgrades and risk.",
        "Read the manifest and lockfile before recommending anything. Check whether the version is "
        "pinned, whether the package is maintained and what the upgrade would break. Recommend the "
        "smallest safe version change and the check that proves it.",
        "read-only",
    ),
    _profile(
        "analyst",
        "Data analyst",
        "Turns data and logs into a short answer backed by numbers.",
        "Answer the question with the numbers that support it and note the range they cover. Say "
        "what the data cannot show before drawing a conclusion. Keep the arithmetic explicit enough "
        "that someone can check it.",
        "read-only",
    ),
)


# Task words that pull each builtin into `select_agents`. Matching is whole-word
# and case-insensitive, so "test" hits "test" and "tests" but never "pytest".
_TRIGGERS: dict[str, tuple[str, ...]] = {
    "planner": ("plan", "planning", "roadmap", "milestone", "scope", "steps", "break down", "outline"),
    "architect": (
        "architect",
        "architecture",
        "design",
        "structure",
        "system design",
        "boundaries",
        "module",
        "layering",
        "tradeoff",
        "pattern",
    ),
    "coder": (
        "implement",
        "implementation",
        "feature",
        "build",
        "write",
        "add",
        "create",
        "function",
        "class",
    ),
    "backend": (
        "auth",
        "authentication",
        "authorization",
        "login",
        "session",
        "server",
        "middleware",
        "backend",
        "jwt",
        "oauth",
        "route",
        "handler",
        "queue",
        "cron",
        "worker",
    ),
    "frontend": (
        "react",
        "vue",
        "svelte",
        "angular",
        "css",
        "tailwind",
        "component",
        "ui",
        "browser",
        "dom",
        "html",
        "layout",
        "hook",
        "nextjs",
        "next.js",
    ),
    "fullstack": ("fullstack", "full-stack", "end to end", "e2e", "client and server"),
    "database": (
        "database",
        "db",
        "sql",
        "postgres",
        "postgresql",
        "mysql",
        "sqlite",
        "schema",
        "migration",
        "query",
        "index",
        "orm",
        "table",
        "transaction",
    ),
    "api": (
        "api",
        "endpoint",
        "rest",
        "graphql",
        "openapi",
        "swagger",
        "contract",
        "payload",
        "request",
        "response",
        "http",
        "sdk",
    ),
    "devops": (
        "deploy",
        "deployment",
        "docker",
        "kubernetes",
        "k8s",
        "ci",
        "cd",
        "pipeline",
        "container",
        "terraform",
        "infra",
        "infrastructure",
        "nginx",
        "release",
        "production",
    ),
    "debugger": (
        "bug",
        "crash",
        "error",
        "exception",
        "traceback",
        "stack trace",
        "debug",
        "failing",
        "broken",
        "reproduce",
        "regression",
        "hang",
        "leak",
    ),
    "reviewer": ("review", "pull request", "pr", "diff", "critique", "feedback", "approve", "nit"),
    "security": (
        "security",
        "auth",
        "vulnerability",
        "xss",
        "csrf",
        "injection",
        "secret",
        "encryption",
        "password",
        "token",
        "permission",
        "sanitize",
        "owasp",
        "exploit",
        "threat",
    ),
    "tester": (
        "test",
        "pytest",
        "unittest",
        "coverage",
        "fixture",
        "mock",
        "assertion",
        "suite",
        "tdd",
        "flaky",
    ),
    "performance": (
        "performance",
        "slow",
        "latency",
        "throughput",
        "memory",
        "cpu",
        "optimize",
        "profiling",
        "benchmark",
        "cache",
        "bottleneck",
        "timeout",
    ),
    "refactor": (
        "refactor",
        "cleanup",
        "clean up",
        "duplicate",
        "duplication",
        "simplify",
        "restructure",
        "extract",
        "rename",
        "technical debt",
        "complexity",
    ),
    "docs": ("docs", "doc", "documentation", "readme", "docstring", "guide", "tutorial", "changelog"),
    "explorer": (
        "explore",
        "find",
        "locate",
        "search",
        "understand",
        "overview",
        "trace",
        "discover",
        "codebase",
        "where",
    ),
    "dependencies": (
        "dependency",
        "package",
        "requirements",
        "pip",
        "npm",
        "lockfile",
        "upgrade",
        "version",
        "install",
        "import",
    ),
    "analyst": (
        "analysis",
        "analyze",
        "metric",
        "data",
        "numbers",
        "insight",
        "report",
        "trend",
        "quantify",
        "logs",
        "statistics",
    ),
}

_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "before",
        "change",
        "changes",
        "code",
        "from",
        "handle",
        "help",
        "into",
        "just",
        "make",
        "need",
        "only",
        "please",
        "should",
        "smallest",
        "task",
        "tasks",
        "that",
        "their",
        "then",
        "there",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "without",
        "work",
        "your",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+.#-]*")


@cache
def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?:s|es)?(?![a-z0-9])")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _profile_words(profile: AgentProfile) -> set[str]:
    words = _tokens(f"{profile.name} {profile.description}")
    return {word for word in words if len(word) >= 5 and word not in _STOPWORDS}


def _score(profile: AgentProfile, lowered: str, tokens: set[str]) -> int:
    score = 0
    for term in _TRIGGERS.get(profile.id, ()):
        if _term_pattern(term).search(lowered):
            score += 3
    if profile.id.lower() in tokens:
        score += 6
    score += 2 * len(_profile_words(profile) & tokens)
    return score


def agents_dir() -> Path:
    """Directory that holds custom profiles; honours THIRTYCLI_HOME."""
    return home() / "agents"


def load_custom_agents(directory: Path | None = None) -> list[AgentProfile]:
    """Read ``*.toml`` profiles; unreadable or invalid files are skipped."""
    target = Path(directory) if directory is not None else agents_dir()
    if not target.is_dir():
        return []
    profiles: list[AgentProfile] = []
    for path in sorted(target.glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        profile = _profile_from_mapping(data, path.stem)
        if profile is not None:
            profiles.append(profile)
    return profiles


def _profile_from_mapping(data: dict, stem: str) -> AgentProfile | None:
    instructions = _text(data.get("instructions"))
    if not instructions:
        return None
    agent_id = _text(data.get("id")) or stem
    if not agent_id:
        return None
    permission = _text(data.get("permission")).lower() or "controlled"
    if permission not in PERMISSIONS:
        permission = "controlled"
    return AgentProfile(
        id=agent_id,
        name=_text(data.get("name")) or agent_id,
        description=_text(data.get("description")) or instructions.splitlines()[0][:120],
        instructions=instructions,
        allowed_tools=_tool_list(data.get("allowed_tools")),
        model=_text(data.get("model")),
        permission=permission,
        context_policy=_text(data.get("context_policy")) or "shared",
        output_contract=_text(data.get("output_contract")) or DEFAULT_OUTPUT,
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _tool_list(value: object) -> tuple[str, ...] | None:
    if isinstance(value, str):
        items = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        return None
    tools = tuple(item.strip() for item in items if item.strip())
    return tools or None


def _catalog(directory: Path | None = None) -> dict[str, AgentProfile]:
    merged = {profile.id.lower(): profile for profile in BUILTIN_AGENTS}
    for profile in load_custom_agents(directory):
        merged[profile.id.lower()] = profile
    return merged


def get_agent(agent_id: str) -> AgentProfile | None:
    """Look up one profile by id, case-insensitively; custom profiles win."""
    key = (agent_id or "").strip().lower()
    return _catalog().get(key) if key else None


def list_agents(directory: Path | None = None) -> list[AgentProfile]:
    """Every builtin plus custom profile, sorted by display name."""
    return sorted(_catalog(directory).values(), key=lambda profile: (profile.name.lower(), profile.id))


def select_agents(text: str, limit: int = 3) -> list[AgentProfile]:
    """Pick the profiles whose keywords best match the task text."""
    if limit <= 0:
        return []
    lowered = text.lower()
    tokens = _tokens(text)
    ranked = sorted(
        ((_score(profile, lowered, tokens), profile) for profile in list_agents()),
        key=lambda item: (-item[0], item[1].name.lower(), item[1].id),
    )
    matched = [profile for score, profile in ranked if score > 0]
    if not matched:
        fallback = get_agent("coder")
        return [fallback] if fallback else []
    return matched[:limit]


_PERMISSION_NOTES = {
    "read-only": (
        "Read-only: you cannot modify files or run shell commands. Inspect and report; "
        "never pretend to have changed something."
    ),
    "controlled": (
        "Controlled: inspect freely, but ask the user before writing files or running commands "
        "that change the workspace."
    ),
    "autonomous": (
        "Autonomous: act within the project policy without asking first. Keep every change scoped "
        "to the task and report what you did."
    ),
}


def permission_note(profile: AgentProfile) -> str:
    """One-line description of what the profile may do on its own."""
    return _PERMISSION_NOTES.get(profile.permission, _PERMISSION_NOTES["controlled"])


def agent_system_prompt(profile: AgentProfile, skills: str = "") -> str:
    """Build the system prompt a runner hands to a model for this profile."""
    lines = [
        f"You are {profile.name}, the {profile.id} agent inside ThirtyCLI.",
        profile.description,
        "",
        "## Instructions",
        profile.instructions.strip(),
        "",
        f"## Permission ({profile.permission})",
        permission_note(profile),
    ]
    if profile.allowed_tools is not None:
        tools = ", ".join(profile.allowed_tools) or "none"
        lines += ["", "## Tools", f"Use only these tools: {tools}."]
    lines += ["", "## Output", profile.output_contract.strip()]
    extra = skills.strip()
    if extra:
        lines += ["", "## Skills", extra]
    return "\n".join(lines).strip() + "\n"
