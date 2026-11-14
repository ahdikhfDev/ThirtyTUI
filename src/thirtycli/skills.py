"""Skill registry: small, reusable playbooks injected only when the prompt asks for them.

Skills are cheap to describe and expensive to keep in every system prompt, so the
registry stays lazy: :func:`select_skills` scores keywords against the current
request and only the winners reach the model via :func:`skill_context`.

Custom skills live as Markdown files in ``~/.thirtycli/skills`` (override the
root with ``THIRTYCLI_HOME``) and shadow builtins with the same name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import home

DEFAULT_LIMIT = 3
SKILLS_DIR_NAME = "skills"

__all__ = [
    "BUILTIN_SKILLS",
    "DEFAULT_LIMIT",
    "Skill",
    "all_skills",
    "get_skill",
    "load_custom_skills",
    "select_skills",
    "skill_context",
]


@dataclass(frozen=True)
class Skill:
    """One reusable playbook: a name, a short description, and how to apply it."""

    name: str
    description: str
    instructions: str
    keywords: tuple[str, ...]
    tools: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


BUILTIN_SKILLS: tuple[Skill, ...] = (
    Skill(
        name="React Development",
        description="Build predictable React components with honest state and effects.",
        instructions=(
            "- Prefer small function components; lift state only when two siblings need it.\n"
            "- Keep hooks top-level: never inside loops, conditions, or nested functions.\n"
            "- Derive values during render instead of syncing state in useEffect.\n"
            "- Use useEffect only for external systems (DOM, subscriptions, network).\n"
            "- Memoize with useMemo/useCallback after a measured render problem, not before."
        ),
        keywords=(
            "react",
            "jsx",
            "tsx",
            "hooks",
            "usestate",
            "useeffect",
            "usecontext",
            "usereducer",
            "component",
            "props",
        ),
        tools=("read_file", "write_file", "edit_file", "search_text"),
        examples=("Why does this component re-render twice?",),
    ),
    Skill(
        name="Next.js Development",
        description="Choose the right Next.js rendering boundary and keep data fetching flat.",
        instructions=(
            "- Default to Server Components; add \"use client\" only where interactivity starts.\n"
            "- Fetch data in Server Components or Route Handlers, not in client effects.\n"
            "- Run mutations through Server Actions or Route Handlers, then revalidate.\n"
            "- Keep secrets server-side; only NEXT_PUBLIC_* values may reach the browser.\n"
            "- Use dynamic rendering deliberately and cache with explicit revalidate windows."
        ),
        keywords=(
            "next.js",
            "nextjs",
            "app router",
            "pages router",
            "server component",
            "server components",
            "server action",
            "server actions",
            "vercel",
            "revalidate",
        ),
        tools=("read_file", "write_file", "edit_file", "search_text"),
        examples=("Should this page be a server or client component?",),
    ),
    Skill(
        name="Laravel Development",
        description="Keep Laravel controllers thin and Eloquent queries explicit.",
        instructions=(
            "- Put business rules in services or actions; keep controllers to validate and delegate.\n"
            "- Use Form Requests for validation and API Resources for response shaping.\n"
            "- Eager-load relationships to kill N+1 queries; check with DB::listen when unsure.\n"
            "- Wrap multi-table writes in DB::transaction and prefer migrations over ad hoc SQL.\n"
            "- Run `php artisan test` (or Pest) after changes and keep queues idempotent."
        ),
        keywords=("laravel", "eloquent", "artisan", "blade", "php", "composer", "phpunit"),
        tools=("read_file", "write_file", "edit_file", "search_text", "run_shell"),
        examples=("Move this validation out of the controller.",),
    ),
    Skill(
        name="Python Development",
        description="Write typed, testable Python that fails loudly and early.",
        instructions=(
            "- Keep functions small and pure where possible; pass dependencies as arguments.\n"
            "- Add type hints on public functions and let the checker catch shape errors.\n"
            "- Use pathlib, context managers, and specific exceptions instead of bare except.\n"
            "- Raise errors with context (`raise ... from exc`) rather than swallowing them.\n"
            "- Run the project's pytest and ruff commands before calling the work done."
        ),
        keywords=(
            "python",
            "pip",
            "venv",
            "virtualenv",
            "asyncio",
            "pandas",
            "numpy",
            "django",
            "flask",
            "type hints",
            "pyproject",
        ),
        tools=("read_file", "write_file", "edit_file", "search_text", "run_shell"),
        examples=("Refactor this script into typed functions.",),
    ),
    Skill(
        name="Rust Development",
        description="Work with ownership instead of fighting the borrow checker.",
        instructions=(
            "- Model ownership first: who owns the data, who borrows it, and for how long.\n"
            "- Prefer borrowing (&T, &str) in signatures; clone only at real boundaries.\n"
            "- Use enums plus match for state instead of sentinel values or flag booleans.\n"
            "- Propagate errors with Result and `?`; reserve unwrap/expect for proven invariants.\n"
            "- Finish with `cargo fmt`, `cargo clippy -- -D warnings`, and `cargo test`."
        ),
        keywords=(
            "rust",
            "cargo",
            "borrow checker",
            "ownership",
            "lifetime",
            "lifetimes",
            "trait",
            "crate",
            "clippy",
            "rustc",
        ),
        tools=("read_file", "write_file", "edit_file", "search_text", "run_shell"),
        examples=("Why does this function need a mutable borrow?",),
    ),
    Skill(
        name="API Design",
        description="Design HTTP APIs that are predictable, versionable, and cheap to call.",
        instructions=(
            "- Name resources as plural nouns and let verbs come from HTTP methods.\n"
            "- Return the right status codes: 201 on create, 204 on empty, 4xx for caller errors.\n"
            "- Make list endpoints paginate, filter, and sort explicitly; cap page size.\n"
            "- Keep writes idempotent where clients retry, and accept an idempotency key.\n"
            "- Version breaking changes and document every endpoint with a schema."
        ),
        keywords=(
            "api",
            "endpoint",
            "endpoints",
            "rest",
            "restful",
            "graphql",
            "http",
            "openapi",
            "status code",
            "pagination",
            "idempotent",
        ),
        tools=("read_file", "write_file", "edit_file", "web_search"),
        examples=("Design the endpoints for team invitations.",),
    ),
    Skill(
        name="Database Design",
        description="Shape schemas and queries that stay correct as data grows.",
        instructions=(
            "- Start from access patterns: list the queries the feature must answer first.\n"
            "- Normalize to third normal form, then denormalize only with a measured reason.\n"
            "- Add foreign keys, NOT NULL, and unique constraints so bad states are impossible.\n"
            "- Index the columns used in filters and joins; check plans for sequential scans.\n"
            "- Ship schema changes as reversible migrations and backfill before tightening rules."
        ),
        keywords=(
            "database",
            "schema",
            "sql",
            "postgres",
            "postgresql",
            "sqlite",
            "mysql",
            "migration",
            "migrations",
            "index",
            "indexes",
            "normalization",
            "foreign key",
        ),
        tools=("read_file", "write_file", "edit_file", "run_shell"),
        examples=("Review this schema for missing indexes.",),
    ),
    Skill(
        name="Debugging",
        description="Find the root cause before changing code.",
        instructions=(
            "- Reproduce the failure first, and keep the smallest command that shows it.\n"
            "- Read the whole traceback bottom-up: the last frame in your code is the suspect.\n"
            "- Form one hypothesis at a time and test it with a log, print, or probe.\n"
            "- Fix the cause, not the symptom; no silent try/except or defensive retries.\n"
            "- Add a test that fails before the fix and passes after it."
        ),
        keywords=(
            "debug",
            "debugging",
            "traceback",
            "stack trace",
            "exception",
            "error",
            "bug",
            "crash",
            "panic",
            "segfault",
            "reproduce",
        ),
        tools=("read_file", "search_text", "run_shell", "git_diff"),
        examples=("This works locally but fails in CI.",),
    ),
    Skill(
        name="Testing",
        description="Write tests that pin behaviour and fail for one reason.",
        instructions=(
            "- Test behaviour through public entry points, not private helpers.\n"
            "- Cover the happy path, each edge case, and the failure you just fixed.\n"
            "- Keep one logical assertion per test and a name that states the expected outcome.\n"
            "- Use fixtures for setup and tmp_path for filesystem work; no sleeps, no real network.\n"
            "- Run the smallest test file first, then the suite, and report real output."
        ),
        keywords=(
            "test",
            "tests",
            "testing",
            "pytest",
            "unit test",
            "integration test",
            "coverage",
            "fixture",
            "mock",
            "assert",
            "tdd",
            "e2e",
        ),
        tools=("read_file", "write_file", "edit_file", "run_shell"),
        examples=("Add a regression test for this bug.",),
    ),
    Skill(
        name="Security Review",
        description="Look for the ways this code can be made to do what it should not.",
        instructions=(
            "- Treat every request field, header, path, and file as untrusted input.\n"
            "- Check authz on each route, not just authn; verify resource ownership server-side.\n"
            "- Stop injection: parameterized SQL, shell arguments as lists, escaped output.\n"
            "- Keep secrets in env or a vault; never in code, logs, URLs, or error messages.\n"
            "- Report findings by severity with the concrete exploit path and a fix."
        ),
        keywords=(
            "security",
            "vulnerability",
            "auth",
            "authentication",
            "authorization",
            "xss",
            "csrf",
            "sql injection",
            "secret",
            "secrets",
            "owasp",
            "permission",
        ),
        tools=("read_file", "search_text", "web_search", "git_diff"),
        examples=("Is this endpoint safe to expose?",),
    ),
    Skill(
        name="Git Workflow",
        description="Keep history readable and revertible.",
        instructions=(
            "- Inspect state first: `git status`, `git diff`, and the recent log.\n"
            "- Commit one logical change at a time with an imperative subject line.\n"
            "- Rebase local work to keep linear history; never rebase shared branches.\n"
            "- Never force-push or reset away work you did not create without asking.\n"
            "- Resolve conflicts by re-reading both sides, then run the tests again."
        ),
        keywords=(
            "git",
            "commit",
            "commits",
            "branch",
            "merge",
            "rebase",
            "pull request",
            "conflict",
            "stash",
            "squash",
        ),
        tools=("run_shell", "git_status", "git_diff", "read_file"),
        examples=("Split this diff into two commits.",),
    ),
    Skill(
        name="Documentation Writing",
        description="Write docs a new teammate can use without asking you.",
        instructions=(
            "- Lead with what the reader must do, then explain why it works that way.\n"
            "- Show a runnable example with real commands and real expected output.\n"
            "- Document inputs, outputs, defaults, and failure modes next to the signature.\n"
            "- Keep one source of truth; link instead of copying drifting snippets.\n"
            "- Cut marketing adjectives; short sentences beat complete sentences."
        ),
        keywords=(
            "documentation",
            "docs",
            "readme",
            "docstring",
            "docstrings",
            "changelog",
            "tutorial",
            "guide",
            "api reference",
        ),
        tools=("read_file", "write_file", "edit_file", "list_dir"),
        examples=("Write the README section for this command.",),
    ),
)


_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _pattern(keyword: str) -> re.Pattern[str]:
    """Compile a case-insensitive keyword pattern with alphanumeric edges."""
    cached = _PATTERN_CACHE.get(keyword)
    if cached is not None:
        return cached
    prefix = r"(?<![a-z0-9])" if keyword[:1].isalnum() else ""
    suffix = r"(?![a-z0-9])" if keyword[-1:].isalnum() else ""
    pattern = re.compile(prefix + re.escape(keyword) + suffix)
    _PATTERN_CACHE[keyword] = pattern
    return pattern


def _occurrences(text: str, keyword: str) -> int:
    key = keyword.strip().lower()
    if not key:
        return 0
    return len(_pattern(key).findall(text))


def _score(skill: Skill, text: str) -> int:
    """Two points per distinct matched keyword plus one per repeat (capped at two).

    Repeats only nudge a skill past a tie; breadth of vocabulary wins.
    """
    matched = 0
    repeats = 0
    for keyword in skill.keywords:
        count = _occurrences(text, keyword)
        if count:
            matched += 1
            repeats += min(count - 1, 2)
    return matched * 2 + repeats


def select_skills(text: str, limit: int = DEFAULT_LIMIT) -> list[Skill]:
    """Return the skills whose keywords appear in ``text``, best match first.

    Ties break by name. A request with no keyword overlap selects nothing, so
    unrelated prompts never drag the whole registry into the system prompt.
    """
    if limit <= 0 or not text or not text.strip():
        return []
    haystack = text.lower()
    scored = [
        (weight, skill)
        for skill in all_skills()
        if (weight := _score(skill, haystack)) > 0
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [skill for _weight, skill in scored[:limit]]


def skill_context(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """Render the selected skills for injection into a system prompt ("" if none)."""
    blocks: list[str] = []
    for skill in select_skills(text, limit):
        lines = [f"## Skill: {skill.name}", skill.instructions.strip()]
        if skill.tools:
            lines.append("Tools: " + ", ".join(skill.tools))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _skills_dir(directory: Path | str | None = None) -> Path:
    return Path(directory).expanduser() if directory is not None else home() / SKILLS_DIR_NAME


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str] | None:
    """Return (metadata, body), or None when the frontmatter block never closes."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            meta: dict[str, str] = {}
            for line in lines[1:index]:
                key, separator, value = line.partition(":")
                if separator and key.strip():
                    meta[key.strip().lower()] = value.strip()
            return meta, "\n".join(lines[index + 1 :])
    return None


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("\"'") for part in value.split(",") if part.strip())


def _scan_body(body: str) -> tuple[str, str]:
    """Pull the first heading and first paragraph out of plain Markdown."""
    heading = ""
    paragraph: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not heading and stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            continue
        if stripped.startswith("#"):
            continue
        if stripped:
            paragraph.append(stripped)
        elif paragraph:
            break
    return heading, " ".join(paragraph).strip()


def _parse_skill_file(path: Path) -> Skill | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        split = _split_frontmatter(raw)
        if split is None:
            return None
        meta, body = split
        body = body.strip()
        if not body:
            return None
        heading, first_paragraph = _scan_body(body)
        name = meta.get("name", "").strip() or heading or path.stem
        description = meta.get("description", "").strip() or first_paragraph
        if not name:
            return None
        return Skill(
            name=name,
            description=description,
            instructions=body,
            keywords=_split_list(meta.get("keywords", "")),
            tools=_split_list(meta.get("tools", "")),
        )
    except (OSError, UnicodeDecodeError):
        return None


def load_custom_skills(directory: Path | str | None = None) -> list[Skill]:
    """Load ``*.md`` skills from a directory (default ``~/.thirtycli/skills``).

    Files may start with ``---`` frontmatter holding ``name``, ``description``,
    ``keywords`` and ``tools`` (comma-separated). Without frontmatter the first
    heading becomes the name and the first paragraph the description. Broken
    files are skipped instead of raising.
    """
    root = _skills_dir(directory)
    if not root.is_dir():
        return []
    skills: list[Skill] = []
    try:
        paths = sorted(root.glob("*.md"))
    except OSError:
        return []
    for path in paths:
        skill = _parse_skill_file(path)
        if skill is not None:
            skills.append(skill)
    skills.sort(key=lambda skill: skill.name.lower())
    return skills


def all_skills(directory: Path | str | None = None) -> list[Skill]:
    """Builtins plus custom skills; a custom skill replaces a same-named builtin."""
    merged: dict[str, Skill] = {skill.name.lower(): skill for skill in BUILTIN_SKILLS}
    for skill in load_custom_skills(directory):
        merged[skill.name.lower()] = skill
    return list(merged.values())


def get_skill(name: str, directory: Path | str | None = None) -> Skill | None:
    """Look a skill up by name, ignoring case and surrounding whitespace."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for skill in all_skills(directory):
        if skill.name.lower() == wanted:
            return skill
    return None
