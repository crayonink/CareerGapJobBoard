"""WCAG contrast, checked against the stylesheet itself.

The palette is two hue numbers and a column of lightnesses. That makes it very
easy to re-tint the whole site - and just as easy to push a pairing below AA
without noticing, because OKLCH lightness is perceptual and contrast is not.

These tests parse the real tokens out of style.css and do the arithmetic, so
changing a token either keeps the site readable or fails the build.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"

#: Hue axes, resolved so tokens written as oklch(... var(--olive)) can be read.
HUES = {"--olive": 118.0, "--pink": 355.0}

TOKEN_RE = re.compile(
    r"(--[a-z0-9-]+):\s*oklch\(([\d.]+)%\s+([\d.]+)\s+(var\(--[a-z]+\)|[\d.]+)\)"
)


def oklch_to_srgb(lightness: float, chroma: float, hue_deg: float) -> tuple[float, ...]:
    hue = math.radians(hue_deg)
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    linear = (
        +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )

    def encode(value: float) -> float:
        value = max(0.0, min(1.0, value))
        return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055

    return tuple(encode(v) for v in linear)


def relative_luminance(rgb: tuple[float, ...]) -> float:
    def linearise(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = (linearise(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture(scope="module")
def tokens() -> dict[str, tuple[float, ...]]:
    css = CSS.read_text(encoding="utf-8")
    found: dict[str, tuple[float, ...]] = {}
    for name, lightness, chroma, hue in TOKEN_RE.findall(css):
        resolved = HUES.get(hue[4:-1]) if hue.startswith("var(") else float(hue)
        if resolved is None:
            continue
        found[name] = oklch_to_srgb(float(lightness) / 100, float(chroma), resolved)
    assert found, "no oklch tokens parsed - the stylesheet or the regex changed"
    return found


# (label, foreground, background, minimum) - AA is 4.5 for body text, 3.0 for
# large or incidental text.
PAIRS = [
    ("body text on the ground", "--text", "--bg", 4.5),
    ("body text on a card", "--text", "--surface", 4.5),
    ("secondary text on the ground", "--text-dim", "--bg", 4.5),
    ("secondary text on a card", "--text-dim", "--surface", 4.5),
    ("faint label on the ground", "--text-faint", "--bg", 3.0),
    ("link on the ground", "--accent", "--bg", 4.5),
    ("accent on a card", "--accent", "--surface", 4.5),
    ("button label on the accent fill", "--accent-ink", "--accent", 4.5),
    ("accent on the soft pill", "--accent", "--accent-soft", 4.5),
    ("secondary text on a neutral pill", "--text-dim", "--surface-2", 4.5),
    ("success text on a card", "--good", "--surface", 4.5),
    ("error text on its banner", "--bad", "--bad-soft", 4.5),
]


@pytest.mark.parametrize("label, fg, bg, minimum", PAIRS, ids=[p[0] for p in PAIRS])
def test_contrast(tokens, label, fg, bg, minimum):
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= minimum, f"{label}: {ratio:.2f}:1, needs {minimum}:1"


def test_the_ground_is_not_white(tokens):
    """The brief was olive, not white. A near-zero-chroma ground would mean
    someone quietly reverted the palette."""
    css = CSS.read_text(encoding="utf-8")
    chroma = float(re.search(r"--bg:\s*oklch\([\d.]+%\s+([\d.]+)", css).group(1))
    assert chroma >= 0.02, f"--bg chroma is {chroma}, which reads as white"


def test_hues_are_olive_and_pink(tokens):
    css = CSS.read_text(encoding="utf-8")
    olive = float(re.search(r"--olive:\s*([\d.]+)", css).group(1))
    pink = float(re.search(r"--pink:\s*([\d.]+)", css).group(1))
    assert 90 <= olive <= 150, f"--olive is {olive}deg, not a yellow-green"
    assert pink >= 320 or pink <= 20, f"--pink is {pink}deg, not a pink"
