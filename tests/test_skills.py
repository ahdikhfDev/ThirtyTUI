from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from thirtycli.skills import (
    BUILTIN_SKILLS,
    Skill,
    all_skills,
    get_skill,
    load_custom_skills,
    select_skills,
    skill_context,
)

REQUIRED = {
    "React Development",
    "Next.js Development",
    "Laravel Development",
    "Python Development",
    "Rust Development",
    "API Design",
    "Database Design",
    "Debugging",
    "Testing",
    "Security Review",
    "Git Workflow",
    "Documentation Writing",
}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def names(skills):
    return [skill.name for skill in skills]


def write_skill(root: Path, filename: str, text: str) -> Path:
    directory = root / "skills"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


# --- builtins -------------------------------------------------------------


def test_builtins_cover_required_skills():
    assert REQUIRED <= set(names(BUILTIN_SKILLS))
    assert len(BUILTIN_SKILLS) >= len(REQUIRED)


def test_builtin_skills_are_filled_in():
    for skill in BUILTIN_SKILLS:
        assert skill.description
        assert skill.keywords
        assert 3 <= len(skill.instructions.splitlines()) <= 6, skill.name


def test_skill_is_frozen():
    skill = Skill("X", "d", "i", ("k",))
    with pytest.raises(FrozenInstanceError):
        skill.name = "Y"  # type: ignore[misc]


# --- selection ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Build a React component with hooks and local state", "React Development"),
        ("Our next.js app router page uses server components", "Next.js Development"),
        ("Add type hints to this python script", "Python Development"),
        ("The request throws a traceback in production", "Debugging"),
        ("Write pytest tests with fixtures", "Testing"),
        ("How should this API endpoint paginate results?", "API Design"),
        ("Design a postgres schema with a foreign key", "Database Design"),
        ("Check this for a security vulnerability", "Security Review"),
        ("Split this diff into two commits and rebase", "Git Workflow"),
        ("Update the README documentation", "Documentation Writing"),
        ("Make the cargo build stop fighting the borrow checker", "Rust Development"),
        ("Move validation out of the Laravel controller into an action", "Laravel Development"),
    ],
)
def test_select_skills_matches_expected_skill(text, expected):
    assert expected in names(select_skills(text))


def test_unrelated_text_selects_nothing():
    assert select_skills("Plan a weekend hike near the coast") == []
    assert select_skills("") == []
    assert select_skills("   ") == []


def test_selection_order_is_deterministic():
    text = "Fix the traceback in our python api, then add pytest tests"
    first = names(select_skills(text, limit=10))
    second = names(select_skills(text, limit=10))
    assert first == second
    assert len(first) >= 3


def test_limit_is_respected():
    text = "Fix the traceback in our python api, then add pytest tests"
    assert len(select_skills(text, limit=1)) == 1
    assert len(select_skills(text, limit=2)) == 2
    assert select_skills(text, limit=0) == []
    assert select_skills(text, limit=-1) == []


def test_react_ranks_above_incidental_matches():
    assert names(select_skills("React component using useState and useEffect"))[0] == "React Development"


# --- skill_context --------------------------------------------------------


def test_skill_context_contains_name_and_instructions():
    context = skill_context("The traceback points at a null bug")
    assert "## Skill: Debugging" in context
    assert "Reproduce the failure first" in context
    assert "Tools:" in context


def test_skill_context_is_empty_without_matches():
    assert skill_context("Plan a weekend hike near the coast") == ""


def test_skill_context_honours_limit():
    text = "Fix the traceback in our python api, then add pytest tests"
    assert skill_context(text, limit=1).count("## Skill:") == 1


# --- custom skills --------------------------------------------------------


def test_custom_skill_with_frontmatter(home):
    write_skill(
        home,
        "deploy.md",
        "---\n"
        "name: Deployment\n"
        "description: Ship safely\n"
        "keywords: deploy, release, rollout\n"
        "tools: run_shell, git_status\n"
        "---\n"
        "Check the changelog, tag, then deploy.\n",
    )

    custom = load_custom_skills()
    assert names(custom) == ["Deployment"]
    skill = custom[0]
    assert skill.description == "Ship safely"
    assert skill.keywords == ("deploy", "release", "rollout")
    assert skill.tools == ("run_shell", "git_status")
    assert "Check the changelog" in skill.instructions
    assert names(select_skills("plan the rollout for friday")) == ["Deployment"]


def test_custom_skill_without_frontmatter(home):
    write_skill(
        home,
        "notes.md",
        "# Release Notes\n"
        "\n"
        "Write the user-facing summary before tagging.\n"
        "\n"
        "Second paragraph is ignored.\n",
    )

    skill = load_custom_skills()[0]
    assert skill.name == "Release Notes"
    assert skill.description == "Write the user-facing summary before tagging."
    assert skill.keywords == ()
    assert skill.tools == ()


def test_custom_skill_overrides_builtin(home):
    write_skill(
        home,
        "react.md",
        "---\nname: React Development\nkeywords: react\n---\nUse our house style for React.\n",
    )

    merged = all_skills()
    assert names(merged).count("React Development") == 1
    override = get_skill("react development")
    assert override is not None
    assert override.instructions == "Use our house style for React."
    assert names(select_skills("react component")) == ["React Development"]
    assert select_skills("react component")[0].instructions == "Use our house style for React."


def test_broken_files_are_ignored(home):
    write_skill(home, "unclosed.md", "---\nname: Broken\nkeywords: broken\n# no closing fence\n")
    write_skill(home, "empty.md", "")
    write_skill(home, "frontmatter-only.md", "---\nname: Bare\n---\n")
    write_skill(home, "good.md", "---\nname: Good\nkeywords: good\n---\nDo the good thing.\n")

    assert names(load_custom_skills()) == ["Good"]
    assert all_skills()  # still usable
    assert get_skill("broken") is None


def test_custom_directory_argument(tmp_path):
    directory = tmp_path / "elsewhere"
    directory.mkdir()
    (directory / "ops.md").write_text(
        "---\nname: Ops\nkeywords: ops\n---\nRun the ops checklist.\n", encoding="utf-8"
    )

    assert names(load_custom_skills(directory)) == ["Ops"]
    assert get_skill("ops", directory) is not None
    assert get_skill("ops") is None


def test_missing_directory_returns_empty_list(tmp_path):
    assert load_custom_skills(tmp_path / "nope") == []


# --- registry -------------------------------------------------------------


def test_all_skills_has_no_duplicates():
    merged = all_skills()
    lowered = [skill.name.lower() for skill in merged]
    assert len(lowered) == len(set(lowered))
    assert len(merged) == len(BUILTIN_SKILLS)


def test_get_skill_is_case_insensitive():
    found = get_skill("  gIT workflow ")
    assert found is not None
    assert found.name == "Git Workflow"
    assert get_skill("nope") is None
    assert get_skill("") is None
