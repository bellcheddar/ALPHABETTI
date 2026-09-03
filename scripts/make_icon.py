#!/usr/bin/env python3
"""Generate the app icon, favicon and Open Graph image.

The icon is the app's own output rather than a generic glyph: three amino acid
letters stacked as GIBBERISH would stack them, at heights taken from a real
information content profile, wrapped on a backbone arc. An icon made from what
the thing actually does beats a picture of a protein.

Writes:
    static/img/icon.svg     300 x 300, the favicon and the launcher beacon
    static/img/og.png       1200 x 630 Open Graph card (written as SVG then
                            rasterised if a rasteriser is available)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "img"

# Neon palette, matching static/css/alphabetti.css.
VOID = "#06060e"
PANEL = "#101020"
CYAN = "#26f5e0"
CYAN_DIM = "#17b8a8"
MAGENTA = "#ff2d9b"
FG = "#e6e9f5"
DIM = "#6b7398"

FONT = "'Baloo 2', 'Arial Black', sans-serif"

# ---------------------------------------------------------------------------
# Real Baloo 2 outlines, not <text>
# ---------------------------------------------------------------------------
#
# An SVG that sets type in <text> depends on the renderer having the font.
# Nothing outside this app does: the favicon, the Open Graph card and anything
# that rasterises the file would all silently fall back to a system face, and
# the icon would stop being the app's own letterforms. So the glyphs are
# converted to path data here, from the same variable font instantiated at the
# same weight as the 3D glyphs.

FONT_TTF = ROOT / "static" / "fonts" / "Baloo2-source.ttf"
_GLYPH_CACHE: dict[str, tuple[str, float, float]] = {}


def glyph_path(character: str) -> tuple[str, float, float]:
    """Return (SVG path data, width, cap height) for one letter at 1000 upem."""
    if character in _GLYPH_CACHE:
        return _GLYPH_CACHE[character]

    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(FONT_TTF)
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": 700})
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()

    # The wordmark needs the whole alphabet, not just the twenty residues.
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ord(char) not in cmap:
            continue
        pen = SVGPathPen(glyph_set)
        glyph_set[cmap[ord(char)]].draw(pen)
        from fontTools.pens.boundsPen import BoundsPen
        bounds_pen = BoundsPen(glyph_set)
        glyph_set[cmap[ord(char)]].draw(bounds_pen)
        x_min, y_min, x_max, y_max = bounds_pen.bounds or (0, 0, 0, 0)
        _GLYPH_CACHE[char] = (pen.getCommands(), x_max - x_min, y_max - y_min)
    return _GLYPH_CACHE[character]


def letter(character: str, cx: float, baseline: float, size: float,
           fill: str, opacity: float = 1.0, rotate: float = 0.0) -> str:
    """One outlined letter, centred on cx and sitting on `baseline`.

    SVG y grows downward and font coordinates grow upward, so the glyph is
    flipped. Scaling is by cap height rather than em, matching how the 3D
    glyphs are normalised, so a `size` here means the same thing it does there.
    """
    path, width, _ = glyph_path(character)
    _, _, cap = glyph_path("H")
    scale = size / cap
    x = cx - (width * scale) / 2
    transform = f"translate({x:.2f} {baseline:.2f}) scale({scale:.5f} {-scale:.5f})"
    if rotate:
        transform = f"rotate({rotate} {cx:.1f} {baseline:.1f}) " + transform
    opacity_attr = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return f'<path d="{path}" transform="{transform}" fill="{fill}"{opacity_attr}/>'



def icon_svg() -> str:
    """300 x 300. A three-letter stack on a backbone arc, glowing."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300"
     width="300" height="300" role="img" aria-label="ALPHABETTI">
  <defs>
    <radialGradient id="ground" cx="50%" cy="42%" r="72%">
      <stop offset="0%" stop-color="#141830"/>
      <stop offset="100%" stop-color="{VOID}"/>
    </radialGradient>
    <filter id="glow" x="-70%" y="-70%" width="240%" height="240%">
      <feGaussianBlur stdDeviation="7" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="b"/>
               <feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="softglow" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="3.4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>

  <rect width="300" height="300" rx="52" fill="url(#ground)"/>
  <rect x="1.5" y="1.5" width="297" height="297" rx="50.5"
        fill="none" stroke="{CYAN}" stroke-opacity="0.28" stroke-width="3"/>

  <!-- The ghost backbone: the same low-opacity tube the app draws. -->
  <path d="M 44 214 C 92 236, 118 168, 150 158 C 186 147, 208 100, 256 96"
        fill="none" stroke="{DIM}" stroke-opacity="0.5"
        stroke-width="15" stroke-linecap="round"/>

  <!-- A GIBBERISH stack: three letters, heights in proportion to p_a * R_i,
       tallest at the bottom, standing on the backbone. -->
  <g filter="url(#softglow)">
    {letter("M", 150, 150, 86, CYAN)}
    {letter("Q", 150, 96, 52, FG, 0.92)}
    {letter("I", 150, 58, 34, MAGENTA, 0.85)}
  </g>

  <!-- Two more positions, smaller, to say "this is a sequence" rather than
       "this is a letter". -->
  <g opacity="0.85">
    {letter("V", 62, 212, 48, CYAN_DIM, rotate=-14)}
    {letter("K", 244, 100, 44, CYAN_DIM, rotate=12)}
  </g>

  <!-- The bit-scale reference bar, which is what makes it a logo and not
       decoration. -->
  <g opacity="0.75">
    <rect x="34" y="252" width="3" height="22" fill="{CYAN}"/>
    <rect x="34" y="252" width="12" height="3" fill="{CYAN}"/>
    <rect x="34" y="271" width="12" height="3" fill="{CYAN}"/>
    <text x="54" y="270" font-family="ui-monospace, monospace" font-size="17"
          fill="{CYAN}" opacity="0.95">1 bit</text>
  </g>
</svg>
"""


def og_svg() -> str:
    """1200 x 630 Open Graph card."""
    letters = [
        (250, 430, 118, CYAN, "M"), (250, 336, 74, FG, "Q"), (250, 278, 46, MAGENTA, "I"),
        (430, 400, 96, CYAN, "F"), (430, 326, 60, FG, "V"),
        (610, 452, 104, CYAN, "K"), (610, 372, 56, FG, "T"), (610, 322, 36, MAGENTA, "L"),
        (790, 408, 88, CYAN, "G"), (790, 340, 52, FG, "S"),
        (960, 438, 100, CYAN, "R"), (960, 362, 58, FG, "E"),
    ]
    glyphs = "\n".join(
        "    " + letter(character, x, y, size, fill)
        for x, y, size, fill, character in letters
    )
    # Advance by each glyph's own width rather than a fixed step. A fixed
    # advance leaves a visible hole before the narrow letters, so "ALPHABETTI"
    # reads as "ALPHABETT I".
    _, _, cap = glyph_path("H")
    size, tracking, pen_x = 62.0, 9.0, 74.0
    parts = []
    for character in "ALPHABETTI":
        _, width, _ = glyph_path(character)
        advance = width * size / cap
        parts.append(letter(character, pen_x + advance / 2, 112, size, "#eafffb"))
        pen_x += advance + tracking
    wordmark = "".join(parts)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630"
     width="1200" height="630">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#141830"/>
      <stop offset="60%" stop-color="#080a16"/>
      <stop offset="100%" stop-color="{VOID}"/>
    </linearGradient>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="1200" height="630" fill="url(#g)"/>

  <path d="M 150 470 C 300 520, 360 400, 500 430 C 660 464, 700 372, 860 400
           C 980 422, 1010 470, 1090 452"
        fill="none" stroke="{DIM}" stroke-opacity="0.42" stroke-width="17"
        stroke-linecap="round"/>

  <g filter="url(#glow)">
{glyphs}
  </g>

  <g filter="url(#glow)">
{wordmark}
  </g>
  <text x="62" y="150" font-family="ui-monospace, monospace" font-size="19"
        fill="{DIM}">A protein sequence logo, in 3D, wrapped around its own predicted structure</text>
  <text x="62" y="580" font-family="ui-monospace, monospace" font-size="19"
        fill="{CYAN}">no multiple sequence alignment anywhere in the pipeline</text>
  <text x="1140" y="580" font-family="ui-monospace, monospace" font-size="18"
        fill="{DIM}" text-anchor="end">alphabetti.mdeller.com</text>
</svg>
"""


def rasterise(svg_path: Path, png_path: Path, width: int) -> bool:
    """Try the tools that might be on this machine. Failure is not fatal."""
    chrome = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if Path(chrome).exists():
        # Chrome renders SVG faithfully and is present wherever this project is
        # developed, which the dedicated tools are not.
        height = int(width * 630 / 1200) if "og" in svg_path.name else width
        try:
            subprocess.run([
                chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
                "--default-background-color=00000000",
                f"--window-size={width},{height}",
                f"--screenshot={png_path}", f"file://{svg_path}",
            ], check=True, capture_output=True, timeout=90)
            if png_path.exists():
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    for command in (
        ["rsvg-convert", "-w", str(width), "-o", str(png_path), str(svg_path)],
        ["inkscape", str(svg_path), "--export-type=png",
         f"--export-filename={png_path}", f"--export-width={width}"],
        ["magick", "-density", "192", str(svg_path), "-resize", f"{width}x",
         str(png_path)],
        ["qlmanage", "-t", "-s", str(width), "-o", str(png_path.parent),
         str(svg_path)],
    ):
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=90)
            if png_path.exists():
                return True
        except (FileNotFoundError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired):
            continue
    return False


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon.svg").write_text(icon_svg())
    (OUT / "og.svg").write_text(og_svg())
    print(f"wrote {OUT/'icon.svg'}")
    print(f"wrote {OUT/'og.svg'}")

    if rasterise(OUT / "og.svg", OUT / "og.png", 1200):
        print(f"wrote {OUT/'og.png'}")
    else:
        print("no SVG rasteriser found; og.png not generated "
              "(og.svg is committed and can be converted later)")
    if rasterise(OUT / "icon.svg", OUT / "icon-300.png", 300):
        print(f"wrote {OUT/'icon-300.png'}")
