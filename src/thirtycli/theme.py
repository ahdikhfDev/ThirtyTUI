"""Palette and rich theme, lifted from the ThirtyCLI website tokens."""

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

# Brand + interface colours (same hex values as the site's CSS tokens).
ORANGE = "#FFB547"
ORANGE_DIM = "#A97A2E"
INK = "#1A1205"
BLUE = "#42A0FF"
GREEN = "#61C985"
RED = "#FF6B6B"
BG_CANVAS = "#1A1A1A"
BG_FRAME = "#0D0D0D"
BG_CONTROL = "#212121"
MUTED = "#979797"
DIM = "#6A6A6A"
TEXT = "#EDEDED"
LINE = "#2A2A2A"

THEME = Theme(
    {
        "accent": f"bold {ORANGE}",
        "accent.dim": ORANGE_DIM,
        "ink": INK,
        "link": BLUE,
        "muted": MUTED,
        "dim": DIM,
        "text": TEXT,
        "ok": f"bold {GREEN}",
        "err": f"bold {RED}",
        "tool": f"italic {ORANGE}",
        "rule": LINE,
    }
)

console = Console(theme=THEME, highlight=False, soft_wrap=True)

# The mark, drawn in half blocks: rounded square, two eyes, a prompt mouth.
MARK_ROWS = (
    "\u2584\u2584\u2584\u2584\u2584\u2584\u2584\u2584\u2584",
    "\u2588       \u2588",
    "\u2588 \u25cf   \u25cf \u2588",
    "\u2588  >_   \u2588",
    "\u2588       \u2588",
    "\u2580\u2580\u2580\u2580\u2580\u2580\u2580\u2580\u2580",
)

PROMPT = ">_ "


def prompt_text() -> Text:
    return Text(PROMPT, style="accent")


def mark(styled: bool = True) -> Text:
    """The mark as a single renderable block."""
    body = Text("\n".join(MARK_ROWS))
    if styled:
        body.stylize("accent")
    return body
