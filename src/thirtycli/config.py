"""Config file handling: ~/.thirtycli/config.toml (override with THIRTYCLI_HOME)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomllib

DEFAULT_SYSTEM = (
    "You are ThirtyCLI, a compact terminal assistant. "
    "Be direct: answer first, then the smallest useful detail. "
    "Reply in the language the user writes in. "
    "Put code in fenced blocks with a language tag. "
    "You can read, write and edit files anywhere on this computer: absolute paths are fine, "
    "and anything outside the workspace is confirmed with the user first. "
    "When you call a tool, say in one short line what you are doing."
)

APP_DIR_NAME = ".thirtycli"


def home() -> Path:
    override = os.environ.get("THIRTYCLI_HOME")
    return Path(override).expanduser() if override else Path.home() / APP_DIR_NAME


def config_path() -> Path:
    return home() / "config.toml"


@dataclass
class Config:
    provider: str = "openai"
    model: str = ""
    temperature: float = 0.2
    base_url: str = ""
    api_key: str = ""
    fallback_model: str = ""
    fallback_provider: str = ""
    system: str = DEFAULT_SYSTEM
    tools: bool = True
    auto_yes: bool = False
    extra: dict = field(default_factory=dict)

    def to_toml(self) -> str:
        lines = [
            "[model]",
            f'provider = "{_esc(self.provider)}"',
            f'model = "{_esc(self.model)}"',
            f"temperature = {float(self.temperature)}",
            f'base_url = "{_esc(self.base_url)}"',
            f'api_key = "{_esc(self.api_key)}"',
            f'fallback_model = "{_esc(self.fallback_model)}"',
            f'fallback_provider = "{_esc(self.fallback_provider)}"',
            "",
            "[chat]",
            f"tools = {str(bool(self.tools)).lower()}",
            f"auto_yes = {str(bool(self.auto_yes)).lower()}",
            f'system = "{_esc(self.system)}"',
            "",
        ]
        for key, value in self.extra.items():
            lines.append(f"{_esc(key)} = {_toml_value(value)}")
        return "\n".join(lines)


def _esc(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{_esc(str(value))}"'


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    model = data.get("model", {})
    chat = data.get("chat", {})
    known = {
        "provider",
        "model",
        "temperature",
        "base_url",
        "api_key",
        "fallback_model",
        "fallback_provider",
        "system",
        "tools",
        "auto_yes",
    }
    return Config(
        provider=str(model.get("provider", "openai")),
        model=str(model.get("model", "")),
        temperature=float(model.get("temperature", 0.2)),
        base_url=str(model.get("base_url", "")),
        api_key=str(model.get("api_key", "")),
        fallback_model=str(model.get("fallback_model", "")),
        fallback_provider=str(model.get("fallback_provider", "")),
        system=str(chat.get("system", DEFAULT_SYSTEM)),
        tools=bool(chat.get("tools", True)),
        auto_yes=bool(chat.get("auto_yes", False)),
        extra={k: v for k, v in data.items() if k not in {"model", "chat"} and k not in known},
    )


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.to_toml(), encoding="utf-8")
    return path


SETTABLE = {
    "provider",
    "model",
    "temperature",
    "base_url",
    "api_key",
    "fallback_model",
    "fallback_provider",
    "system",
    "tools",
    "auto_yes",
}


def set_value(cfg: Config, key: str, raw: str) -> Config:
    if key not in SETTABLE:
        raise KeyError(key)
    if key == "temperature":
        value: object = float(raw)
    elif key in {"tools", "auto_yes"}:
        value = raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        value = raw
    setattr(cfg, key, value)
    return cfg


def as_dict(cfg: Config) -> dict:
    return asdict(cfg)


def load_env_files() -> None:
    """Minimal .env reader: cwd first, then ~/.thirtycli/.env. Never overrides."""
    for path in (Path.cwd() / ".env", home() / ".env"):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
