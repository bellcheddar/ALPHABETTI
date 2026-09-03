#!/usr/bin/env python3
"""Convert a TTF to the typeface.json format three.js reads.

Baloo 2 is the house display face and it is what the 3D glyphs are set in. It
ships from Google Fonts as a variable font with a 400-800 weight axis, so it is
instantiated at 700 (Bold) before conversion.

The format is a JSON object of glyph outlines, each a space-separated command
string. The argument order is the part worth being careful about, because it is
not the order anyone would guess and getting it wrong produces letters that are
subtly, unmistakably wrong rather than absent:

    m x y                          moveTo
    l x y                          lineTo
    q  endX endY  ctrlX ctrlY      quadratic: END POINT FIRST
    b  endX endY  c1x c1y c2x c2y  cubic:     END POINT FIRST

three.js reads the end point before the control points for both curve types
(see Font.js in the three.js source). Emitting them in the usual control-first
order gives a letterform that still closes and still renders, with every curve
bulging the wrong way.

Usage:
    python3 scripts/make_typeface.py /path/to/font.ttf out.typeface.json [weight]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

# Only what the app draws: the twenty canonical amino acids, plus X for unknown
# residues. Every other glyph in a 1,601-glyph font is dead weight in a file the
# browser has to download before anything appears.
NEEDED = "ACDEFGHIKLMNPQRSTVWYX"


class TypefacePen(BasePen):
    """Collects a glyph outline as a three.js typeface command string."""

    def __init__(self, glyphSet):
        super().__init__(glyphSet)
        self.commands: list[str] = []

    def _moveTo(self, pt):
        self.commands.append(f"m {_n(pt[0])} {_n(pt[1])}")

    def _lineTo(self, pt):
        self.commands.append(f"l {_n(pt[0])} {_n(pt[1])}")

    def _curveToOne(self, cp1, cp2, pt):
        # End point first. See the module docstring.
        self.commands.append(
            f"b {_n(pt[0])} {_n(pt[1])} {_n(cp1[0])} {_n(cp1[1])} "
            f"{_n(cp2[0])} {_n(cp2[1])}"
        )

    def _qCurveToOne(self, cp, pt):
        self.commands.append(f"q {_n(pt[0])} {_n(pt[1])} {_n(cp[0])} {_n(cp[1])}")

    def _closePath(self):
        # typeface.json has no explicit close command; three.js closes each
        # subpath implicitly at the next moveTo and at the end.
        pass


def _n(value: float) -> str:
    """Round to whole units. At 1000 units per em this is well below a pixel."""
    return str(int(round(value)))


def convert(ttf_path: Path, out_path: Path, weight: float | None = 700) -> dict:
    font = TTFont(ttf_path)

    if "fvar" in font and weight is not None:
        axes = {a.axisTag: (a.minValue, a.maxValue) for a in font["fvar"].axes}
        if "wght" in axes:
            low, high = axes["wght"]
            weight = min(high, max(low, weight))
            font = instancer.instantiateVariableFont(font, {"wght": weight})
            print(f"instantiated at wght={weight}")

    units = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()
    metrics = font["hmtx"].metrics

    glyphs: dict[str, dict] = {}
    missing: list[str] = []
    for character in NEEDED:
        code = ord(character)
        if code not in cmap:
            missing.append(character)
            continue
        name = cmap[code]
        pen = TypefacePen(glyph_set)
        glyph_set[name].draw(pen)
        advance, _ = metrics[name]

        outline = " ".join(pen.commands)
        bounds = _bounds(glyph_set, name, font)
        glyphs[character] = {
            "ha": int(advance),
            "x_min": int(bounds[0]),
            "x_max": int(bounds[2]),
            "o": outline,
        }

    if missing:
        raise SystemExit(f"Font is missing required glyphs: {''.join(missing)}")

    head, hhea, os2 = font["head"], font["hhea"], font["OS/2"]
    data = {
        "glyphs": glyphs,
        "familyName": _family_name(font),
        "ascender": int(hhea.ascent),
        "descender": int(hhea.descent),
        "underlinePosition": int(font["post"].underlinePosition),
        "underlineThickness": int(font["post"].underlineThickness),
        "boundingBox": {
            "yMin": int(head.yMin), "xMin": int(head.xMin),
            "yMax": int(head.yMax), "xMax": int(head.xMax),
        },
        "resolution": int(units),
        "original_font_information": {
            "format": 0,
            "copyright": _name(font, 0),
            "font_family_name": _family_name(font),
            "license": _name(font, 13),
        },
        "cssFontWeight": str(int(weight)) if weight else "normal",
        "cssFontStyle": "normal",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, separators=(",", ":")))
    return data


def _bounds(glyph_set, name, font):
    from fontTools.pens.boundsPen import BoundsPen
    pen = BoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.bounds or (0, 0, 0, 0)


def _name(font, nameID) -> str:
    for record in font["name"].names:
        if record.nameID == nameID:
            try:
                return str(record.toUnicode())
            except Exception:
                continue
    return ""


def _family_name(font) -> str:
    return _name(font, 1) or "Unknown"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    weight = float(sys.argv[3]) if len(sys.argv) > 3 else 700.0
    result = convert(Path(sys.argv[1]), Path(sys.argv[2]), weight)
    size = Path(sys.argv[2]).stat().st_size
    print(f"wrote {sys.argv[2]}  {len(result['glyphs'])} glyphs  {size/1024:.1f} KB")
    print(f"  family: {result['familyName']}  resolution: {result['resolution']}")
