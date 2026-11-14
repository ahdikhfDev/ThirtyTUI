"""Local tools. Paths may go anywhere; the chat loop asks before leaving the workspace."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from langchain_core.tools import BaseTool, tool

MAX_CHARS = 20_000
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
}


class ToolError(RuntimeError):
    pass


def resolve_path(root: Path | str, raw: str) -> tuple[Path, bool]:
    """Resolve a tool path. Returns (path, inside_workspace).

    Absolute paths and `..` are allowed; callers decide whether to ask first.
    """
    root = Path(root).resolve()
    raw = (raw or "").strip()
    if not raw:
        return root, True
    candidate = Path(raw).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    inside = target == root or root in target.parents
    return target, inside


def resolve_in_root(root: Path, raw: str) -> Path:
    """Strict resolution for @file mentions: never leaves the workspace."""
    target, inside = resolve_path(root, raw)
    if not inside:
        raise ToolError(f"Path escapes the workspace: {raw}")
    return target


def _clip(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… truncated ({len(text) - limit} more chars)"


SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
READONLY_NAMES = {
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


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
    return raw.strip()


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=20, context=_ssl_context()) as response:
            payload = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise ToolError(f"HTTP {exc.code} for {url}. The site may block automated requests.") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"Could not reach {url}: {exc.reason}") from exc
    return payload.decode(charset, errors="replace")


_FRAMEWORK_MARKERS: tuple[tuple[str, str], ...] = (
    (r"(?<![\w-])fastapi(?![\w-])", "FastAPI"),
    (r"(?<![\w-])flask(?![\w-])", "Flask"),
    (r"(?<![\w-])django(?![\w-])", "Django"),
    (r"(?<![\w-])starlette(?![\w-])", "Starlette"),
    (r"(?<![\w-])langchain(?![\w-])", "LangChain"),
    (r'"react"', "React"),
    (r'"next"', "Next.js"),
    (r'"vue"', "Vue"),
    (r'"svelte"', "Svelte"),
    (r'"@angular/core"', "Angular"),
    (r'"express"', "Express"),
    (r'"@nestjs/core"', "NestJS"),
    (r"(?<![\w-])actix-web(?![\w-])", "Actix"),
    (r"(?<![\w-])axum(?![\w-])", "Axum"),
    (r"(?<![\w-])rails(?![\w-])", "Rails"),
)

_MANIFESTS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
)

_DEPENDENCY_FILES = (*_MANIFESTS, "pom.xml")

_EXT_LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".swift": "Swift",
    ".lua": "Lua",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".sql": "SQL",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".dart": "Dart",
    ".scala": "Scala",
    ".hs": "Haskell",
}

_LANGUAGE_MARKERS = (
    ("pyproject.toml", "Python"),
    ("setup.py", "Python"),
    ("setup.cfg", "Python"),
    ("package.json", "JavaScript"),
    ("tsconfig.json", "TypeScript"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("pom.xml", "Java"),
)


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _detect_framework(root: Path) -> str:
    text = "\n".join(_read_text(root / name) for name in _MANIFESTS if (root / name).is_file())
    if not text.strip():
        return "no framework detected"
    found = [label for pattern, label in _FRAMEWORK_MARKERS if re.search(pattern, text, re.I)]
    return ", ".join(found) or "no framework detected"


def _detect_language(root: Path) -> str:
    counts: dict[str, int] = {}
    scanned = 0
    for _base, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            language = _EXT_LANGUAGES.get(Path(name).suffix.lower())
            if not language:
                continue
            counts[language] = counts.get(language, 0) + 1
            scanned += 1
        if scanned >= 5_000:
            break
    for name, language in _LANGUAGE_MARKERS:
        if (root / name).is_file():
            counts[language] = counts.get(language, 0) + 1
    rows = [
        f"{language}: {count} file{'s' if count != 1 else ''}"
        for language, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return "\n".join(rows) or "no source files detected"


def _project_tree(root: Path, max_depth: int, max_entries: int) -> str:
    lines: list[str] = ["."]
    count = 0
    truncated = False

    def walk(directory: Path, depth: int) -> None:
        nonlocal count, truncated
        if truncated or depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if count >= max_entries:
                truncated = True
                return
            if entry.name in SKIP_DIRS:
                continue
            lines.append(f"{'  ' * (depth - 1)}{entry.name}{'/' if entry.is_dir() else ''}")
            count += 1
            if entry.is_dir() and not entry.is_symlink():
                walk(entry, depth + 1)

    walk(root, 1)
    if truncated:
        lines.append(f"… truncated at {max_entries} entries")
    return _clip("\n".join(lines))


def _package_scripts(root: Path) -> dict[str, str]:
    path = root / "package.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(_read_text(path))
    except ValueError:
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return {name: body for name, body in scripts.items() if isinstance(body, str)}


def _detect_commands(root: Path) -> dict[str, list[str] | None]:
    """Detect test/lint/format/build commands from project files. None means no idea."""
    python_project = any(
        (root / name).is_file() for name in ("pyproject.toml", "setup.cfg", "pytest.ini")
    )
    ruff = (
        (root / ".ruff.toml").is_file()
        or (root / "ruff.toml").is_file()
        or "[tool.ruff" in _read_text(root / "pyproject.toml")
    )
    cargo = (root / "Cargo.toml").is_file()
    go = (root / "go.mod").is_file()
    scripts = _package_scripts(root)

    def npm(script: str) -> list[str] | None:
        return ["npm", "run", script] if script in scripts else None

    test = None
    if python_project:
        test = [sys.executable, "-m", "pytest", "-q"]
    elif npm("test"):
        test = npm("test")
    elif cargo:
        test = ["cargo", "test"]
    elif go:
        test = ["go", "test", "./..."]

    lint = None
    if ruff:
        lint = [sys.executable, "-m", "ruff", "check", "."]
    elif npm("lint"):
        lint = npm("lint")
    elif cargo:
        lint = ["cargo", "clippy"]

    fmt = npm("format")
    if fmt is None and cargo:
        fmt = ["cargo", "fmt"]

    build = npm("build")
    if build is None and cargo:
        build = ["cargo", "build"]

    return {"test": test, "lint": lint, "format": fmt, "build": build}


def _run_command(root: Path, command: list[str], timeout: int, label: str) -> str:
    if timeout <= 0:
        raise ToolError(f"timeout must be a positive number of seconds, got {timeout}.")
    if timeout > 3_600:
        raise ToolError("timeout is capped at 3600 seconds.")
    executable = shutil.which(command[0])
    if not executable:
        raise ToolError(f"{command[0]} is not installed or not on PATH.")
    try:
        proc = subprocess.run(
            [executable, *command[1:]],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{label} timed out after {timeout}s.") from exc
    except OSError as exc:
        raise ToolError(f"Could not run {label}: {exc}") from exc
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    shown = " ".join([Path(command[0]).name, *command[1:]])
    return _clip(f"$ {shown}\nexit={proc.returncode}\n{output.strip() or '(no output)'}", 8_000)


def _quality(root: Path, key: str, label: str, timeout: int) -> str:
    command = _detect_commands(root).get(key)
    if not command:
        return f"no command detected for {label}; add project config or run it manually."
    return _run_command(root, command, timeout, label)


def build_tools(root: Path | str, allow_shell: bool = True, sandbox=None) -> list[BaseTool]:
    root = Path(root).resolve()
    tools: list[BaseTool] = []

    @tool
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        """Read a text file. Absolute paths are fine. Use end_line=0 to read to the end."""
        target, _inside = resolve_path(root, path)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line)
        end = len(lines) if end_line <= 0 else min(end_line, len(lines))
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
        return _clip(body or "(empty file)")

    @tool
    def write_file(path: str, content: str) -> str:
        """Write text to a file, creating folders as needed. Absolute paths are fine."""
        target, _inside = resolve_path(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {target}"

    @tool
    def edit_file(path: str, old: str, new: str, count: int = 1) -> str:
        """Replace an exact snippet in a file. Fails if the snippet is missing or ambiguous."""
        target, _inside = resolve_path(root, path)
        if not target.is_file():
            raise ToolError(f"Not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        hits = text.count(old)
        if hits == 0:
            raise ToolError(
                "Snippet not found. Read the file first; whitespace and indentation must match exactly."
            )
        if hits > count:
            raise ToolError(
                f"Snippet appears {hits} times. Pass count={hits} to replace all, "
                "or include more surrounding lines to make it unique."
            )
        target.write_text(text.replace(old, new, count), encoding="utf-8")
        removed = len(old.splitlines())
        added = len(new.splitlines())
        return f"Edited {target}: replaced {hits} match(es), {removed} → {added} lines"

    @tool
    def list_dir(path: str = ".") -> str:
        """List files and folders. Absolute paths are fine."""
        target, _inside = resolve_path(root, path)
        if not target.is_dir():
            raise ToolError(f"Not a directory: {path}")
        rows = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.is_dir():
                rows.append(f"{entry.name}/")
            else:
                rows.append(f"{entry.name}  ({entry.stat().st_size:,} B)")
        return _clip("\n".join(rows) or "(empty)")

    @tool
    def search_text(pattern: str, glob: str = "**/*", max_results: int = 40) -> str:
        """Search file contents in the workspace with a regex. Returns path:line: match."""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Bad regex: {exc}") from exc
        hits: list[str] = []
        files = 0
        for path in root.glob(glob):
            if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.stat().st_size > 1_000_000:
                continue
            files += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "\x00" in text[:2048]:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{path.relative_to(root)}:{number}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        return _clip("\n".join(hits) + f"\n… stopped at {max_results} matches ({files} files scanned)")
        return _clip("\n".join(hits) or f"No matches in {files} files.")

    @tool
    def search_files(pattern: str = "**/*", max_results: int = 100) -> str:
        """List workspace paths matching a glob such as '**/*.py'. Noise folders are skipped."""
        if max_results < 1:
            raise ToolError("max_results must be at least 1.")
        raw = (pattern or "").strip() or "**/*"
        if Path(raw).is_absolute() or ".." in Path(raw).parts:
            raise ToolError(f"Pattern must stay inside the workspace: {pattern}")
        limit = min(max_results, 1_000)
        hits: list[str] = []
        for path in sorted(root.glob(raw)):
            if path == root:
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            hits.append(relative.as_posix() + ("/" if path.is_dir() else ""))
            if len(hits) >= limit:
                break
        if not hits:
            return f"No paths match {raw}."
        suffix = f"\n… limited to {limit} results" if len(hits) >= limit else ""
        return _clip("\n".join(hits) + suffix)

    @tool
    def delete_file(path: str, recursive: bool = False) -> str:
        """Delete a file inside the workspace. Directories need recursive=True."""
        raw = (path or "").strip()
        if not raw:
            raise ToolError("delete_file needs a path inside the workspace.")
        target, inside = resolve_path(root, raw)
        if not inside:
            raise ToolError(f"Refusing to delete outside the workspace: {path}")
        if target == root:
            raise ToolError("Refusing to delete the workspace root.")
        lexical = Path(raw).expanduser()
        if not lexical.is_absolute():
            lexical = root / lexical
        if lexical.is_symlink():
            raise ToolError(f"Refusing to delete a symlink: {path}")
        relative = target.relative_to(root).as_posix()
        if target.is_file():
            target.unlink()
            return f"Deleted file: {relative}"
        if target.is_dir():
            if not recursive:
                raise ToolError(f"{relative} is a directory. Pass recursive=True to delete it.")
            shutil.rmtree(target)
            return f"Deleted directory: {relative}"
        raise ToolError(f"Nothing to delete: {path}")

    @tool
    def web_fetch(url: str) -> str:
        """Fetch a web page and return its readable text (scripts and tags removed)."""
        if not url.lower().startswith(("http://", "https://")):
            raise ToolError("Only http(s) URLs are allowed.")
        text = _get(url)
        if "<html" in text[:2000].lower() or "<body" in text[:2000].lower():
            text = _html_to_text(text)
        return _clip(text)

    @tool
    def web_search(query: str, max_results: int = 5) -> str:
        """Search the web (DuckDuckGo) and return titles, links and snippets."""
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        page = _get(url)
        if "anomaly" in page.lower() and "result__a" not in page:
            raise ToolError("Search was rate limited. Try again or use web_fetch on a known URL.")

        links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', page, re.S)
        rows = []
        for index, (href, title) in enumerate(links[:max_results]):
            target = href
            match = re.search(r"uddg=([^&]+)", href)
            if match:
                target = urllib.parse.unquote(match.group(1))
            clean_title = _html_to_text(title)
            snippet = _html_to_text(snippets[index]) if index < len(snippets) else ""
            rows.append(f"{index + 1}. {clean_title}\n   {target}\n   {snippet[:220]}")
        return _clip("\n\n".join(rows) or "No results.")

    @tool
    def git_status() -> str:
        """Show the git branch and changed files for the workspace."""
        return _git(["status", "--short", "--branch"], root)

    @tool
    def git_diff(staged: bool = False) -> str:
        """Show the workspace diff (unstaged by default, staged=True for --cached)."""
        args = ["diff", "--stat", "--cached"] if staged else ["diff", "--stat"]
        summary = _git(args, root)
        detail = _git(["diff", "--cached"] if staged else ["diff"], root)
        return _clip(summary + "\n\n" + detail)

    @tool
    def git_log(limit: int = 10) -> str:
        """Show recent commits (one line each) for the workspace repository."""
        if limit < 1:
            raise ToolError("limit must be at least 1.")
        out = _git(["log", f"-n{min(limit, 100)}", "--oneline", "--decorate"], root)
        if "does not have any commits yet" in out.lower():
            raise ToolError("Repository has no commits yet.")
        return _clip(out)

    @tool
    def git_branch() -> str:
        """Show the current branch of the workspace repository."""
        name = _git(["branch", "--show-current"], root).strip()
        return name if name and not name.startswith("(exit") else "(no commits yet)"

    @tool
    def git_commit(message: str) -> str:
        """Stage everything (git add -A) and commit with the given message."""
        text = (message or "").strip()
        if not text:
            raise ToolError("A commit message is required.")
        _git(["add", "-A"], root)
        if _git(["diff", "--cached", "--name-only"], root).strip() == "(exit 0)":
            raise ToolError("Nothing to commit: the working tree has no changes.")
        out = _git(["commit", "-m", text], root)
        low = out.lower()
        if "please tell me who you are" in low or "author identity unknown" in low:
            raise ToolError("Git user is not configured. Set user.name and user.email first.")
        if "nothing to commit" in low:
            raise ToolError("Nothing to commit: the working tree has no changes.")
        return _clip(out)

    @tool
    def detect_framework() -> str:
        """Guess the project's frameworks from manifest files."""
        return _detect_framework(root)

    @tool
    def detect_language() -> str:
        """Guess the project's languages from file extensions and manifests."""
        return _detect_language(root)

    @tool
    def inspect_dependencies() -> str:
        """Show dependency manifests (pyproject, package.json, requirements, Cargo, go.mod)."""
        chunks = []
        for name in _DEPENDENCY_FILES:
            candidate = root / name
            if candidate.is_file():
                chunks.append(f"## {name}\n{_read_text(candidate, 4_000).strip()}")
        return _clip("\n\n".join(chunks) or "No dependency files found.")

    @tool
    def inspect_project_structure(max_depth: int = 3, max_entries: int = 200) -> str:
        """Show the workspace tree, skipping noise folders (bounded depth and entries)."""
        if max_depth < 1 or max_entries < 1:
            raise ToolError("max_depth and max_entries must be at least 1.")
        return _project_tree(root, min(max_depth, 10), min(max_entries, 2_000))

    @tool
    def run_tests(timeout: int = 300) -> str:
        """Run the detected test command in the workspace; returns exit code and output."""
        return _quality(root, "test", "tests", timeout)

    @tool
    def run_linter(timeout: int = 120) -> str:
        """Run the detected lint command in the workspace; returns exit code and output."""
        return _quality(root, "lint", "lint", timeout)

    @tool
    def run_formatter(timeout: int = 120) -> str:
        """Run the detected formatter in the workspace; returns exit code and output."""
        return _quality(root, "format", "formatter", timeout)

    @tool
    def run_build(timeout: int = 300) -> str:
        """Run the detected build command in the workspace; returns exit code and output."""
        return _quality(root, "build", "build", timeout)

    @tool
    def delegate(task: str, role: str = "worker") -> str:
        """Hand a self-contained subtask to a read-only sub-agent. Issue several calls in one turn to run them in parallel."""
        raise ToolError("delegate is handled by the host, not by the tool runner")

    @tool
    def now() -> str:
        """Current local date and time."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tools.extend(
        [
            read_file,
            write_file,
            edit_file,
            list_dir,
            search_text,
            web_fetch,
            web_search,
            git_status,
            git_diff,
            git_log,
            git_branch,
            git_commit,
            search_files,
            delete_file,
            detect_framework,
            detect_language,
            inspect_dependencies,
            inspect_project_structure,
            run_tests,
            run_linter,
            run_formatter,
            run_build,
            delegate,
            now,
        ]
    )

    if allow_shell:

        @tool
        def run_shell(command: str, timeout: int = 60) -> str:
            """Run a shell command in the workspace. The user is asked to confirm first."""
            if sandbox is not None:
                result = sandbox.run(command, timeout=timeout)
                out = (result.stdout or "") + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
                suffix = " (timed out)" if result.timed_out else ""
                return _clip(f"exit={result.returncode}{suffix}\n{out.strip() or '(no output)'}")
            proc = subprocess.run(
                command,
                shell=True,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
            return _clip(f"exit={proc.returncode}\n{out.strip() or '(no output)'}")

        tools.append(run_shell)

    return tools


def _git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=False
        )
    except FileNotFoundError as exc:
        raise ToolError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("git timed out") from exc
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0 and "not a git repository" in out.lower():
        raise ToolError("This workspace is not a git repository.")
    return out or f"(exit {proc.returncode})"


def build_readonly_tools(root: Path | str) -> list[BaseTool]:
    """Tools a sub-agent may use: no writes, no shell, no delegation."""
    return [tool for tool in build_tools(root, allow_shell=False) if tool.name in READONLY_NAMES]


def run_git(args: list[str], cwd: Path | str) -> str:
    """Thin git wrapper used by /diff and /commit."""
    return _git(args, Path(cwd))


def tool_map(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {t.name: t for t in tools}


def platform_hint() -> str:
    return "PowerShell/cmd" if sys.platform.startswith("win") else "sh"
