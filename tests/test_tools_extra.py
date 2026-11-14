"""Filesystem, git, project-intelligence and quality tools added on top of test_tools.py."""

import shutil
import subprocess

import pytest

from thirtycli.tools import (
    ToolError,
    _detect_commands,
    build_readonly_tools,
    build_tools,
    tool_map,
)

HAS_GIT = shutil.which("git") is not None


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_search_files_finds_matches_and_skips_git(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("noise\n", encoding="utf-8")

    tools = tool_map(build_tools(tmp_path))

    found = tools["search_files"].invoke({"pattern": "**/*.py"})
    assert "src/app.py" in found
    assert "notes.txt" not in found

    everything = tools["search_files"].invoke({"pattern": "**/*", "max_results": 50})
    assert "notes.txt" in everything
    assert "src/" in everything
    assert ".git" not in everything


def test_search_files_rejects_escaping_patterns(tmp_path):
    tools = tool_map(build_tools(tmp_path))
    with pytest.raises(ToolError):
        tools["search_files"].invoke({"pattern": "../*.txt"})
    with pytest.raises(ToolError):
        tools["search_files"].invoke({"pattern": "**/*", "max_results": 0})


def test_delete_file_respects_workspace_and_recursive_flag(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "b.txt").write_text("y", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    tools = tool_map(build_tools(tmp_path))

    out = tools["delete_file"].invoke({"path": "a.txt"})
    assert "Deleted" in out
    assert not (tmp_path / "a.txt").exists()

    with pytest.raises(ToolError):
        tools["delete_file"].invoke({"path": "pkg"})
    assert (tmp_path / "pkg" / "b.txt").is_file()

    with pytest.raises(ToolError):
        tools["delete_file"].invoke({"path": "../outside.txt"})
    with pytest.raises(ToolError):
        tools["delete_file"].invoke({"path": str(outside)})
    assert outside.is_file()

    with pytest.raises(ToolError):
        tools["delete_file"].invoke({"path": "."})
    with pytest.raises(ToolError):
        tools["delete_file"].invoke({"path": str(tmp_path)})
    assert tmp_path.is_dir()

    out = tools["delete_file"].invoke({"path": "pkg", "recursive": True})
    assert "Deleted directory" in out
    assert not (tmp_path / "pkg").exists()


def test_delete_file_missing_path_is_clear(tmp_path):
    tools = tool_map(build_tools(tmp_path))
    with pytest.raises(ToolError):
        tools["delete_file"].invoke({"path": "nope.txt"})


def test_detect_language_and_framework_from_manifests(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastapi"]\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18"}}',
        encoding="utf-8",
    )

    tools = tool_map(build_tools(tmp_path))

    language = tools["detect_language"].invoke({})
    assert "Python" in language
    assert "JavaScript" in language

    framework = tools["detect_framework"].invoke({})
    assert "FastAPI" in framework
    assert "React" in framework


def test_detect_language_unknown_workspace(tmp_path):
    tools = tool_map(build_tools(tmp_path))
    assert "no source files" in tools["detect_language"].invoke({})
    assert "no framework" in tools["detect_framework"].invoke({})


def test_inspect_dependencies_and_structure(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo-dep"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    tools = tool_map(build_tools(tmp_path))

    deps = tools["inspect_dependencies"].invoke({})
    assert "pyproject.toml" in deps
    assert "demo-dep" in deps

    tree = tools["inspect_project_structure"].invoke({"max_depth": 3})
    assert "src/" in tree
    assert "app.py" in tree
    assert "pyproject.toml" in tree

    with pytest.raises(ToolError):
        tools["inspect_project_structure"].invoke({"max_depth": 0})


def test_quality_tools_report_missing_commands(tmp_path):
    tools = tool_map(build_tools(tmp_path))
    for name in ("run_tests", "run_linter", "run_formatter", "run_build"):
        out = tools[name].invoke({})
        assert "no command detected" in out


def test_detect_commands_helper(tmp_path):
    empty = _detect_commands(tmp_path)
    assert empty == {"test": None, "lint": None, "format": None, "build": None}

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n\n[tool.ruff]\nline-length = 100\n',
        encoding="utf-8",
    )
    commands = _detect_commands(tmp_path)
    assert commands["test"] is not None
    assert "-m" in commands["test"] and "pytest" in commands["test"]
    assert commands["lint"] is not None
    assert "-m" in commands["lint"] and "ruff" in commands["lint"]

    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "vite build", "format": "prettier --write ."}}',
        encoding="utf-8",
    )
    commands = _detect_commands(tmp_path)
    assert commands["build"] is not None and "build" in commands["build"]
    assert commands["format"] is not None and "format" in commands["format"]


def test_detect_commands_cargo_and_go(tmp_path):
    cargo = tmp_path / "cargo"
    cargo.mkdir()
    (cargo / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    commands = _detect_commands(cargo)
    assert commands["test"] == ["cargo", "test"]
    assert commands["lint"] == ["cargo", "clippy"]
    assert commands["format"] == ["cargo", "fmt"]
    assert commands["build"] == ["cargo", "build"]

    golang = tmp_path / "go"
    golang.mkdir()
    (golang / "go.mod").write_text("module demo\n", encoding="utf-8")
    commands = _detect_commands(golang)
    assert commands["test"] == ["go", "test", "./..."]


def test_run_tests_executes_a_passing_suite(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    tools = tool_map(build_tools(tmp_path))
    out = tools["run_tests"].invoke({"timeout": 120})
    assert "exit=0" in out
    assert "passed" in out


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_git_tools_roundtrip(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")

    tools = tool_map(build_tools(tmp_path))

    committed = tools["git_commit"].invoke({"message": "first commit"})
    assert "first commit" in committed

    log = tools["git_log"].invoke({"limit": 5})
    assert "first commit" in log

    branch = tools["git_branch"].invoke({})
    assert branch.strip()
    assert "no commits" not in branch

    with pytest.raises(ToolError):
        tools["git_commit"].invoke({"message": "nothing changed"})

    with pytest.raises(ToolError):
        tools["git_commit"].invoke({"message": "   "})


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_git_tools_fail_clearly_outside_a_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    tools = tool_map(build_tools(tmp_path))
    with pytest.raises(ToolError):
        tools["git_log"].invoke({})
    with pytest.raises(ToolError):
        tools["git_branch"].invoke({})
    with pytest.raises(ToolError):
        tools["git_commit"].invoke({"message": "nope"})


def test_legacy_tools_and_readonly_split_are_preserved(tmp_path):
    names = {tool.name for tool in build_tools(tmp_path)}
    legacy = {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_text",
        "web_fetch",
        "web_search",
        "git_status",
        "git_diff",
        "delegate",
        "now",
        "run_shell",
    }
    assert legacy <= names

    readonly = {tool.name for tool in build_readonly_tools(tmp_path)}
    assert "search_files" in readonly
    assert "git_log" in readonly
    assert "git_branch" in readonly
    assert "detect_framework" in readonly
    assert "detect_language" in readonly
    assert "inspect_dependencies" in readonly
    assert "inspect_project_structure" in readonly
    assert not {"delete_file", "git_commit", "run_tests"} & readonly
    assert not {"run_linter", "run_formatter", "run_build"} & readonly
