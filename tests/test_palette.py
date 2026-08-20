"""WCAG contrast, checked against the stylesheet itself.

The palette is two hue tokens and a column of OKLCH lightnesses. That makes it very easy to
restyle the whole site - and just as easy to push a pairing below AA without
noticing, because OKLCH lightness is perceptual and contrast is not.

These tests parse the real tokens out of style.css and do the arithmetic, so
changing a token either keeps the site readable or fails the build. They also
pin the brief itself: the ground stays warm, and the state colours keep their hue.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"

#: Hue axes, resolved so tokens written as oklch(... var(--sand)) can be read.
HUES = {"--sand": 68.0, "--clay": 40.0}

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


#: The tokens that set the mood. A cold or neutral value in any of these means
#: the warm palette has been half-reverted.
WARM_GROUND = [
    "--bg", "--bg-elevated", "--surface", "--surface-2", "--surface-3",
    "--line", "--line-strong",
]


@pytest.mark.parametrize("token", WARM_GROUND)
def test_the_ground_is_warm(token):
    """The brief is warm and human. Publishing a career gap is an exposing
    thing to do, and a cold white product UI makes it feel like filing a claim.
    Chroma at or near zero here means someone reverted to grey."""
    css = CSS.read_text(encoding="utf-8")
    match = re.search(rf"{token}:\s*oklch\([\d.]+%\s+([\d.]+)", css)
    assert match, f"{token} is not defined as an oklch() literal any more"
    chroma = float(match.group(1))
    assert chroma >= 0.008, f"{token} has chroma {chroma}; that reads as grey, not warm"


def test_hues_are_sand_and_clay():
    css = CSS.read_text(encoding="utf-8")
    sand = float(re.search(r"--sand:\s*([\d.]+)", css).group(1))
    clay = float(re.search(r"--clay:\s*([\d.]+)", css).group(1))
    # 20-100deg in OKLCH is the warm arc: red-orange through to gold.
    assert 20 <= sand <= 100, f"--sand is {sand}deg, outside the warm arc"
    assert 20 <= clay <= 100, f"--clay is {clay}deg, outside the warm arc"


def test_ink_is_not_pure_black():
    """True black on cream reads as a hole punched in the paper."""
    css = CSS.read_text(encoding="utf-8")
    chroma = float(re.search(r"--text:\s*oklch\([\d.]+%\s+([\d.]+)", css).group(1))
    assert chroma > 0.01, f"--text chroma is {chroma}; warm ink needs some hue"


def test_state_colours_keep_their_hue(tokens):
    """Asserted so nobody greys these out for tidiness - an error banner that
    is grey is not an error banner."""
    css = CSS.read_text(encoding="utf-8")
    for token in ("--good", "--warn", "--bad"):
        chroma = float(re.search(rf"{token}:\s*oklch\([\d.]+%\s+([\d.]+)", css).group(1))
        assert chroma > 0.05, f"{token} has been desaturated to {chroma}"
