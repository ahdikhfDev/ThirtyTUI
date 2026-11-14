"""Agent catalog: builtin profiles, selection, custom TOML and prompt assembly."""

import pytest

from thirtycli.agent_catalog import (
    BUILTIN_AGENTS,
    PERMISSIONS,
    AgentProfile,
    agent_system_prompt,
    get_agent,
    list_agents,
    load_custom_agents,
    permission_note,
    select_agents,
)

ENGINEERING = {
    "architect",
    "api",
    "backend",
    "coder",
    "database",
    "debugger",
    "devops",
    "frontend",
    "fullstack",
    "planner",
}
QUALITY = {"performance", "refactor", "reviewer", "security", "tester"}
RESEARCH = {"analyst", "dependencies", "docs", "explorer"}
EXPECTED_IDS = ENGINEERING | QUALITY | RESEARCH


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def write_agent(home, filename, body):
    folder = home / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_text(body, encoding="utf-8")
    return path


def test_builtin_ids_cover_the_catalog_and_are_unique():
    ids = [profile.id for profile in BUILTIN_AGENTS]
    assert len(ids) == len(set(ids))
    assert set(ids) == EXPECTED_IDS


@pytest.mark.parametrize("profile", BUILTIN_AGENTS, ids=lambda profile: profile.id)
def test_builtin_profiles_are_usable(profile):
    assert profile.name.strip()
    assert profile.description.strip()
    assert len(profile.instructions.split()) >= 10
    assert profile.permission in PERMISSIONS
    assert profile.context_policy == "shared"
    assert profile.output_contract.strip()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("fix the auth middleware in the login flow", {"backend", "security"}),
        ("react component re-renders on every keystroke", {"frontend"}),
        ("pytest keeps failing on the test_login case", {"tester"}),
        ("add a database schema migration for orders", {"database"}),
        ("set up the docker deploy pipeline", {"devops"}),
        ("please refactor the duplicate parsing code", {"refactor"}),
        ("update the readme documentation", {"docs"}),
        ("upgrade the npm dependency lockfile", {"dependencies"}),
        ("the api endpoint returns a payload for each request", {"api"}),
        ("the crash throws a traceback on startup", {"debugger"}),
        ("review the diff before it lands", {"reviewer"}),
    ],
)
def test_select_agents_matches_task_keywords(text, expected):
    picked = select_agents(text)
    assert picked
    assert picked[0].id in expected
    assert len(picked) <= 3


def test_select_agents_falls_back_to_coder_for_generic_text():
    picked = select_agents("please handle this task")
    assert [profile.id for profile in picked] == ["coder"]


def test_select_agents_honours_the_limit_and_order():
    text = "auth middleware login react component database schema docker deploy"
    assert len(select_agents(text, limit=2)) == 2
    assert len(select_agents(text, limit=1)) == 1
    assert select_agents(text, limit=0) == []
    assert select_agents(text, limit=5) == select_agents(text, limit=5)


def test_select_agents_is_deterministic():
    text = "fix the slow api endpoint query"
    first = [profile.id for profile in select_agents(text)]
    second = [profile.id for profile in select_agents(text)]
    assert first == second
    assert "performance" in first
    assert "api" in first


def test_get_agent_is_case_insensitive():
    assert get_agent("REVIEWER").id == "reviewer"
    assert get_agent("  Security ").id == "security"
    assert get_agent("nope") is None
    assert get_agent("") is None


def test_list_agents_is_sorted_by_name_and_has_no_duplicates():
    agents = list_agents()
    assert len(agents) == len(EXPECTED_IDS)
    names = [profile.name.lower() for profile in agents]
    assert names == sorted(names)
    assert {profile.id for profile in agents} == EXPECTED_IDS


def test_custom_toml_overrides_a_builtin(home):
    write_agent(
        home,
        "reviewer.toml",
        """
id = "reviewer"
name = "Strict reviewer"
description = "Blocks merges until the diff is testable."
instructions = "Read the diff and list only blocking defects, each with the test that proves it."
allowed_tools = ["read_file", "git_diff"]
model = "gpt-5-mini"
permission = "read-only"
context_policy = "isolated"
output_contract = "Bullet list, five items max."
""",
    )
    custom = load_custom_agents()
    assert len(custom) == 1
    profile = get_agent("Reviewer")
    assert profile.name == "Strict reviewer"
    assert profile.permission == "read-only"
    assert profile.allowed_tools == ("read_file", "git_diff")
    assert profile.model == "gpt-5-mini"
    assert profile.context_policy == "isolated"
    assert profile.output_contract == "Bullet list, five items max."
    reviewers = [agent for agent in list_agents() if agent.id == "reviewer"]
    assert len(reviewers) == 1
    assert reviewers[0].name == "Strict reviewer"


def test_custom_agent_is_used_by_selection(home):
    write_agent(
        home,
        "strict_review.toml",
        'id = "reviewer"\nname = "Strict reviewer"\n'
        'description = "Blocks merges until the diff is testable."\n'
        'instructions = "Read the diff and list only blocking defects."\n',
    )
    picked = select_agents("review the diff")
    assert picked[0].id == "reviewer"
    assert picked[0].name == "Strict reviewer"


def test_load_custom_agents_reads_an_explicit_directory(tmp_path):
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "sre.toml").write_text(
        'id = "sre"\nname = "SRE"\ndescription = "Keeps the service up."\n'
        'instructions = "Look at the alert, name the failing dependency, propose one fix."\n'
        'allowed_tools = "read_file git_status"\n'
        'permission = "autonomous"\n',
        encoding="utf-8",
    )
    custom = load_custom_agents(folder)
    assert [profile.id for profile in custom] == ["sre"]
    assert custom[0].allowed_tools == ("read_file", "git_status")
    assert custom[0].permission == "autonomous"
    assert custom[0].output_contract  # falls back to the default contract


def test_broken_and_incomplete_toml_files_are_ignored(home):
    write_agent(home, "broken.toml", "id = [unclosed\ninstructions = ")
    write_agent(home, "no_instructions.toml", 'id = "ghost"\nname = "Ghost"\n')
    write_agent(home, "empty.toml", 'name = "Empty"\ninstructions = "   "\n')
    assert load_custom_agents() == []
    assert get_agent("ghost") is None
    assert get_agent("reviewer").name == "Code reviewer"


def test_unknown_permission_falls_back_to_controlled(tmp_path):
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "wild.toml").write_text(
        'id = "wild"\ninstructions = "Do the thing and report the files you touched."\n'
        'permission = "unhinged"\n',
        encoding="utf-8",
    )
    profile = load_custom_agents(folder)[0]
    assert profile.permission == "controlled"
    assert profile.name == "wild"


def test_agent_system_prompt_carries_name_permission_contract_and_skills():
    profile = get_agent("security")
    prompt = agent_system_prompt(profile, skills="- **security-audit**: scan for leaked secrets")
    assert profile.name in prompt
    assert "## Permission (read-only)" in prompt
    assert permission_note(profile) in prompt
    assert profile.output_contract in prompt
    assert "## Skills" in prompt
    assert "security-audit" in prompt
    assert prompt.endswith("\n")


def test_agent_system_prompt_omits_empty_skills_and_lists_tools():
    profile = get_agent("explorer")
    prompt = agent_system_prompt(profile)
    assert "## Skills" not in prompt
    assert "## Tools" in prompt
    assert "search_text" in prompt
    assert profile.output_contract in prompt


@pytest.mark.parametrize(
    ("permission", "needle"),
    [
        ("read-only", "cannot modify"),
        ("controlled", "ask the user"),
        ("autonomous", "without asking"),
    ],
)
def test_permission_note_explains_each_level(permission, needle):
    profile = AgentProfile(id="x", name="X", description="", instructions="", permission=permission)
    assert needle in permission_note(profile)


def test_output_contract_override_reaches_the_prompt():
    profile = AgentProfile(
        id="brief",
        name="Brief agent",
        description="Answers briefly.",
        instructions="Answer in one line.",
        output_contract="One sentence, no preamble.",
    )
    prompt = agent_system_prompt(profile)
    assert "One sentence, no preamble." in prompt
    assert "Ask before" not in prompt
    assert "ask the user" in prompt  # default permission is controlled
