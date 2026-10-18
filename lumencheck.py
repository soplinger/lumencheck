#!/usr/bin/env python3
"""Lumen Check: offline WCAG colour-contrast checker.

Parses hex, rgb() and hsl() colours, computes WCAG 2.x contrast ratios,
suggests the nearest passing shade, simulates colour-vision deficiency and
checks whole palettes from a plain-text file with line-numbered errors.
Standard library only; no network access.
"""

import argparse
import colorsys
import json
import re
import sys

AA_NORMAL = 4.5
AA_LARGE = 3.0
AAA_NORMAL = 7.0
AAA_LARGE = 4.5

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
RGB_RE = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.IGNORECASE)
HSL_RE = re.compile(
    r"^hsl\(\s*(\d{1,3}(?:\.\d+)?)\s*,\s*(\d{1,3}(?:\.\d+)?)%\s*,\s*(\d{1,3}(?:\.\d+)?)%\s*\)$",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
PAIR_FLAGS = ("large", "aaa")

# Approximate dichromacy matrices, applied to linear RGB.
CVD_MATRICES = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}

LEVEL_ROWS = (
    ("AA", False, AA_NORMAL),
    ("AA", True, AA_LARGE),
    ("AAA", False, AAA_NORMAL),
    ("AAA", True, AAA_LARGE),
)


class ColourError(ValueError):
    """A colour string could not be parsed."""


class PaletteError(ValueError):
    """A palette file has one or more line-numbered errors."""


def parse_colour(text):
    """Return an (r, g, b) tuple of ints from #rgb, #rrggbb, rgb() or hsl()."""
    value = text.strip()
    if not value:
        raise ColourError("empty colour")
    match = HEX_RE.match(value)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    if value.startswith("#"):
        raise ColourError(f"'{value}' is not #rgb or #rrggbb")
    match = RGB_RE.match(value)
    if match:
        channels = tuple(int(group) for group in match.groups())
        if any(channel > 255 for channel in channels):
            raise ColourError(f"rgb channel above 255 in '{value}'")
        return channels
    match = HSL_RE.match(value)
    if match:
        hue, sat, light = (float(group) for group in match.groups())
        if hue >= 360 or sat > 100 or light > 100:
            raise ColourError(f"hsl value out of range in '{value}' (h < 360, s and l 0-100)")
        red, green, blue = colorsys.hls_to_rgb(hue / 360, light / 100, sat / 100)
        return tuple(round(channel * 255) for channel in (red, green, blue))
    raise ColourError(
        f"unrecognised colour '{value}' (use #rgb, #rrggbb, rgb(r, g, b) or hsl(h, s%, l%))"
    )


def to_hex(rgb):
    return "#%02x%02x%02x" % rgb


def _linear(channel):
    value = channel / 255
    if value <= 0.03928:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _encoded(value):
    if value <= 0.0031308:
        return 12.92 * value
    return 1.055 * value ** (1 / 2.4) - 0.055


def luminance(rgb):
    """WCAG 2.x relative luminance, from 0 (black) to 1 (white)."""
    red, green, blue = (_linear(channel) for channel in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    """WCAG contrast ratio between two colours, from 1 to 21."""
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def threshold(level="AA", large=False):
    if level == "AAA":
        return AAA_LARGE if large else AAA_NORMAL
    return AA_LARGE if large else AA_NORMAL


def _from_hls(hue, light, sat):
    red, green, blue = colorsys.hls_to_rgb(hue, light, sat)
    return tuple(round(channel * 255) for channel in (red, green, blue))


def nearest_passing(foreground, background, target):
    """Smallest lightness change of the foreground that reaches the target.

    Returns (rgb, signed_change) or None if no shade of this hue can pass.
    A signed change above zero means the shade got lighter.
    """
    if contrast_ratio(foreground, background) >= target:
        return foreground, 0.0
    hue, light, sat = colorsys.rgb_to_hls(*(channel / 255 for channel in foreground))
    best = None
    for sign in (-1, 1):
        for step in range(1, 1001):
            delta = step / 1000
            candidate_light = light + sign * delta
            if not 0 <= candidate_light <= 1:
                break
            candidate = _from_hls(hue, candidate_light, sat)
            if contrast_ratio(candidate, background) >= target:
                if best is None or delta < abs(best[1]):
                    best = (candidate, sign * delta)
                break
    return best


def simulate(rgb, kind):
    """Approximate how a colour looks to someone with the given deficiency."""
    linear = [_linear(channel) for channel in rgb]
    out = []
    for row in CVD_MATRICES[kind]:
        value = sum(weight * component for weight, component in zip(row, linear))
        value = min(1.0, max(0.0, value))
        out.append(round(_encoded(value) * 255))
    return tuple(out)


def parse_palette(text, source="palette"):
    """Parse a palette file into (colours, pairs). Raises PaletteError on problems.

    Colour lines are 'name = colour'. Pair lines are 'pair FG BG [large] [aaa]'.
    Lines starting with '#' or ';' are comments.
    """
    colours = {}
    defined_on = {}
    pairs = []
    errors = []

    def fail(lineno, message):
        errors.append((lineno, f"{source}:{lineno}: {message}"))

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" in line:
            name, _, value = (part.strip() for part in line.partition("="))
            if not NAME_RE.match(name):
                fail(lineno, f"invalid colour name '{name}'")
            elif name in defined_on:
                fail(lineno, f"duplicate colour name '{name}' (first defined on line {defined_on[name]})")
            else:
                try:
                    colours[name] = parse_colour(value)
                    defined_on[name] = lineno
                except ColourError as exc:
                    fail(lineno, str(exc))
            continue
        tokens = line.split()
        if tokens[0].lower() != "pair":
            fail(lineno, f"unrecognised line '{line}' (expected 'name = colour' or 'pair FG BG [large] [aaa]')")
            continue
        words = tokens[1:]
        flags = {word.lower() for word in words if word.lower() in PAIR_FLAGS}
        names = [word for word in words if word.lower() not in PAIR_FLAGS]
        if len(names) != 2:
            fail(lineno, "pair needs exactly two colour names, e.g. 'pair ink paper large'")
            continue
        fg_name, bg_name = names
        if fg_name == bg_name:
            fail(lineno, f"pair uses '{fg_name}' on both sides")
            continue
        pairs.append((fg_name, bg_name, "large" in flags, "aaa" in flags, lineno))

    for fg_name, bg_name, _, _, lineno in pairs:
        for name in (fg_name, bg_name):
            if name not in colours:
                fail(lineno, f"unknown colour '{name}' in pair")

    if errors:
        errors.sort(key=lambda item: item[0])
        raise PaletteError("\n".join(message for _, message in errors))
    return colours, pairs


def load_palette(path):
    with open(path, encoding="utf-8") as handle:
        return parse_palette(handle.read(), source=path)


def evaluate_pairs(colours, pairs):
    results = []
    for fg_name, bg_name, large, aaa, lineno in pairs:
        ratio = contrast_ratio(colours[fg_name], colours[bg_name])
        level = "AAA" if aaa else "AA"
        required = threshold(level, large)
        results.append({
            "line": lineno,
            "foreground": fg_name,
            "background": bg_name,
            "level": level,
            "large": large,
            "ratio": round(ratio, 2),
            "required": required,
            "pass": ratio >= required,
        })
    return results


def _text_size(large):
    return "large" if large else "normal"


def cmd_convert(args):
    rgb = parse_colour(args.colour)
    red, green, blue = (channel / 255 for channel in rgb)
    hue, light, sat = colorsys.rgb_to_hls(red, green, blue)
    print(f"hex        {to_hex(rgb)}")
    print(f"rgb        rgb({rgb[0]}, {rgb[1]}, {rgb[2]})")
    print(f"hsl        hsl({hue * 360:.0f}, {sat * 100:.0f}%, {light * 100:.0f}%)")
    print(f"luminance  {luminance(rgb):.4f}")
    return EXIT_OK


def cmd_check(args):
    foreground = parse_colour(args.foreground)
    background = parse_colour(args.background)
    ratio = contrast_ratio(foreground, background)
    if args.json:
        report = {
            "foreground": to_hex(foreground),
            "background": to_hex(background),
            "ratio": round(ratio, 2),
            "passes": {
                f"{level}_{_text_size(large)}": ratio >= target
                for level, large, target in LEVEL_ROWS
            },
        }
        print(json.dumps(report, indent=2))
        return EXIT_OK
    print(f"{to_hex(foreground)} on {to_hex(background)}  {ratio:.2f}:1")
    for level, large, target in LEVEL_ROWS:
        verdict = "pass" if ratio >= target else "fail"
        print(f"  {level:<4} {_text_size(large) + ' text':<11} >= {target:.1f}   {verdict}")
    return EXIT_OK


def cmd_fix(args):
    foreground = parse_colour(args.foreground)
    background = parse_colour(args.background)
    target = threshold(args.level, args.large)
    before = contrast_ratio(foreground, background)
    label = f"{args.level} {_text_size(args.large)} text ({target:.1f}:1)"
    if before >= target:
        print(f"{to_hex(foreground)} on {to_hex(background)} already passes {label}: {before:.2f}:1")
        return EXIT_OK
    found = nearest_passing(foreground, background, target)
    if found is None:
        print(f"no shade of {to_hex(foreground)} reaches {label} on {to_hex(background)}")
        return EXIT_FAIL
    shade, signed = found
    direction = "lighter" if signed > 0 else "darker"
    print(f"{to_hex(foreground)} on {to_hex(background)} fails {label}: {before:.2f}:1")
    print(
        f"nearest passing shade: {to_hex(shade)} ({contrast_ratio(shade, background):.2f}:1), "
        f"{direction} by {abs(signed) * 100:.1f} lightness points"
    )
    return EXIT_OK


def cmd_simulate(args):
    foreground = parse_colour(args.foreground)
    background = parse_colour(args.background)
    print(f"{to_hex(foreground)} on {to_hex(background)}  {contrast_ratio(foreground, background):.2f}:1 normal vision")
    for kind in CVD_MATRICES:
        sim_fg = simulate(foreground, kind)
        sim_bg = simulate(background, kind)
        ratio = contrast_ratio(sim_fg, sim_bg)
        verdict = "pass" if ratio >= AA_NORMAL else "fail"
        print(f"  {kind:<13} {to_hex(sim_fg)} on {to_hex(sim_bg)}  {ratio:.2f}:1  AA normal {verdict}")
    return EXIT_OK


def cmd_matrix(args):
    colours, _ = load_palette(args.palette)
    if not colours:
        print(f"{args.palette}: no colours defined")
        return EXIT_OK
    target = threshold(args.level)
    names = list(colours)
    width = max(8, max(len(name) for name in names))
    print(" " * width + "".join(f"  {name:>{width}}" for name in names))
    failures = 0
    for fg_name in names:
        cells = []
        for bg_name in names:
            if fg_name == bg_name:
                cells.append("-")
                continue
            ratio = contrast_ratio(colours[fg_name], colours[bg_name])
            failed = ratio < target
            failures += failed
            cells.append(f"{ratio:.2f}{'!' if failed else ''}")
        print(f"{fg_name:<{width}}" + "".join(f"  {cell:>{width}}" for cell in cells))
    print(f"\n! = below {args.level} normal text ({target:.1f}:1); {failures} failing cell(s)")
    return EXIT_OK


def cmd_lint(args):
    colours, pairs = load_palette(args.palette)
    print(f"{args.palette}: OK, {len(colours)} colours, {len(pairs)} pairs")
    return EXIT_OK


def cmd_pairs(args):
    colours, pairs = load_palette(args.palette)
    if not pairs:
        print(f"{args.palette}: no pair lines declared")
        return EXIT_OK
    results = evaluate_pairs(colours, pairs)
    passed = sum(result["pass"] for result in results)
    if args.json:
        print(json.dumps({"pairs": results, "passed": passed, "total": len(results)}, indent=2))
    else:
        for result in results:
            label = f"{result['foreground']} on {result['background']} ({result['level']} {_text_size(result['large'])})"
            verdict = "pass" if result["pass"] else "FAIL"
            print(
                f"{result['line']:>4}  {label:<36} {result['ratio']:>6.2f}:1  "
                f"need {result['required']:.1f}:1  {verdict}"
            )
        print(f"\n{passed} of {len(results)} pairs pass")
    return EXIT_OK if passed == len(results) else EXIT_FAIL


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lumencheck",
        description="Offline WCAG colour-contrast checker.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    convert = sub.add_parser("convert", help="show a colour in hex, rgb, hsl and luminance")
    convert.add_argument("colour")
    convert.set_defaults(handler=cmd_convert)

    check = sub.add_parser("check", help="contrast ratio and all four WCAG levels for a pair")
    check.add_argument("foreground")
    check.add_argument("background")
    check.add_argument("--json", action="store_true", help="print a JSON report")
    check.set_defaults(handler=cmd_check)

    fix = sub.add_parser("fix", help="find the nearest shade of the foreground that passes")
    fix.add_argument("foreground")
    fix.add_argument("background")
    fix.add_argument("--level", choices=["AA", "AAA"], default="AA")
    fix.add_argument("--large", action="store_true", help="use large-text thresholds")
    fix.set_defaults(handler=cmd_fix)

    simulate_cmd = sub.add_parser("simulate", help="contrast under protanopia, deuteranopia and tritanopia")
    simulate_cmd.add_argument("foreground")
    simulate_cmd.add_argument("background")
    simulate_cmd.set_defaults(handler=cmd_simulate)

    matrix = sub.add_parser("matrix", help="ratio grid for every colour in a palette file")
    matrix.add_argument("palette")
    matrix.add_argument("--level", choices=["AA", "AAA"], default="AA")
    matrix.set_defaults(handler=cmd_matrix)

    lint = sub.add_parser("lint", help="validate a palette file and report line-numbered errors")
    lint.add_argument("palette")
    lint.set_defaults(handler=cmd_lint)

    pairs = sub.add_parser("pairs", help="check declared pairs; exits 1 if any fail (CI gate)")
    pairs.add_argument("palette")
    pairs.add_argument("--json", action="store_true", help="print a JSON report")
    pairs.set_defaults(handler=cmd_pairs)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ValueError, OSError) as exc:
        print(f"lumencheck: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
