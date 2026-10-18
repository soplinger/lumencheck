# Lumen Check

**Offline WCAG colour-contrast checker for designers, developers and CI.**

Lumen Check takes the colours in your design and tells you exactly which text
pairs fail WCAG 2.x, how far off they are, what the nearest passing shade would
be, and how they look to people with colour-vision deficiency. It reads plain
text palette files with line-numbered errors and exits non-zero in CI when a
declared pair fails. It is a single stdlib-only Python file: no network, no
accounts, no API keys.

## Features

- **Four colour notations**: `#rgb`, `#rrggbb`, `rgb(r, g, b)` and `hsl(h, s%, l%)`, all validated with clear errors.
- **Exact WCAG 2.x contrast ratios** from the relative-luminance formula, shown to two decimals.
- **All four WCAG levels at once**: AA and AAA, each for normal and large text, with pass/fail per level.
- **Nearest passing shade**: `fix` searches lightness in HSL, keeping the hue and saturation, and reports the smallest change that reaches the target.
- **Colour-vision simulation**: `simulate` shows how a pair looks under protanopia, deuteranopia and tritanopia, and whether it still passes.
- **Colour conversion**: `convert` prints hex, rgb, hsl and relative luminance for any colour.
- **Palette files**: named colours plus `pair` declarations, with `large` and `aaa` flags, in one readable text file.
- **Palette matrix**: `matrix` prints the ratio for every colour against every other, marking cells below the chosen level.
- **Line-numbered lint**: bad colours, duplicate names, unknown names in pairs and malformed lines are all reported as `file:line: message`.
- **CI gate**: `pairs` exits `1` when any declared pair fails, so it can block a merge.
- **JSON output** for `check` and `pairs`, for scripts and dashboards.
- **Clear exit codes**: `0` pass, `1` a failure was found, `2` the input was invalid.

## Quick start

Check one pair:

```console
$ python3 lumencheck.py check '#767676' '#ffffff'
#767676 on #ffffff  4.54:1
  AA   normal text >= 4.5   pass
  AA   large text  >= 3.0   pass
  AAA  normal text >= 7.0   fail
  AAA  large text  >= 4.5   pass
```

Ask for the nearest passing shade of a colour. `fix` prints the original ratio, then the shade it found, its new ratio, and how many lightness points it moved:

```console
$ python3 lumencheck.py fix '#c7d2fe' '#ffffff' --large
```

Run every declared pair in the example palette:

```console
$ python3 lumencheck.py pairs examples/brand.palette
```

The example palette includes a deliberately failing pair, so this command exits `1`.

## Palette file format

```text
# Lines starting with # or ; are comments.
ink = #1a1a1a
paper = #ffffff
muted = rgb(118, 118, 118)
link = hsl(225, 83%, 45%)

pair ink paper
pair muted paper large
pair link paper aaa
```

- `name = colour` defines a colour. Names start with a letter and may contain letters, digits, `-` and `_`.
- `pair FG BG [large] [aaa]` declares a pair to check. Without flags it is AA normal text. `large` uses the large-text threshold, and `aaa` checks AAA instead of AA.

See `examples/brand.palette` for a complete file.

## Commands

| Command | What it does |
| --- | --- |
| `convert COLOUR` | Show a colour in hex, rgb, hsl and luminance |
| `check FG BG [--json]` | Ratio and all four WCAG levels for a pair |
| `fix FG BG [--level AA\|AAA] [--large]` | Nearest passing shade of FG |
| `simulate FG BG` | Contrast under three colour-vision deficiencies |
| `matrix FILE [--level AA\|AAA]` | Ratio grid for every colour in a palette |
| `lint FILE` | Validate a palette file with line-numbered errors |
| `pairs FILE [--json]` | Check declared pairs; exits `1` if any fail |

## How to run

Python 3.8 or newer. No install step and no dependencies.

```console
$ python3 lumencheck.py --help
$ python3 lumencheck.py convert '#0b6e4f'
```

To use it as a CI gate, add a step that runs `python3 lumencheck.py pairs path/to/your.palette`. The step fails whenever a declared pair is below its target.

## How to test

The tests use the standard library `unittest` module, so they run without installing anything:

```console
$ python3 -m unittest discover -s tests -v
```

If you have pytest installed, it runs the same tests:

```console
$ python3 -m pytest tests
```

The test suite covers colour parsing and rejection of bad input, luminance and
contrast maths against known values, the WCAG threshold table, the nearest-shade
search (including the impossible case), colour-vision simulation, palette
parsing with line-numbered errors, pair evaluation, and the CLI exit codes and
JSON output.

## Notes on accuracy

- Contrast ratios follow WCAG 2.x relative luminance. WCAG 3 is not implemented.
- Colour-vision simulation uses approximate dichromacy matrices applied in linear RGB. Treat it as a quick visual check, not a clinical model.
- Large text means 18 pt regular or 14 pt bold and above, as defined by WCAG. The tool does not know the font size, so you choose `large` yourself.
