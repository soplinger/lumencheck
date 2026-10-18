import contextlib
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import lumencheck as lc  # noqa: E402

EXAMPLE = os.path.join(ROOT, "examples", "brand.palette")
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def run(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = lc.main(argv)
    return code, out.getvalue(), err.getvalue()


class ParseColourTests(unittest.TestCase):
    def test_hex_short_and_long_forms(self):
        self.assertEqual(lc.parse_colour("#abc"), (0xAA, 0xBB, 0xCC))
        self.assertEqual(lc.parse_colour("#AABBCC"), (0xAA, 0xBB, 0xCC))

    def test_rgb_and_hsl_forms(self):
        self.assertEqual(lc.parse_colour("rgb( 10 , 20,30 )"), (10, 20, 30))
        self.assertEqual(lc.parse_colour("hsl(0, 100%, 50%)"), (255, 0, 0))
        self.assertEqual(lc.parse_colour("hsl(240, 0%, 100%)"), WHITE)

    def test_invalid_colours_are_rejected(self):
        for bad in ["", "#12", "#gggggg", "#aabbccdd", "fff", "rgb(256,0,0)", "hsl(360,10%,10%)"]:
            with self.subTest(colour=bad):
                with self.assertRaises(lc.ColourError):
                    lc.parse_colour(bad)


class ContrastTests(unittest.TestCase):
    def test_luminance_extremes(self):
        self.assertAlmostEqual(lc.luminance(BLACK), 0.0, places=6)
        self.assertAlmostEqual(lc.luminance(WHITE), 1.0, places=6)

    def test_black_on_white_is_21(self):
        self.assertAlmostEqual(lc.contrast_ratio(BLACK, WHITE), 21.0, places=6)

    def test_grey_767676_on_white_passes_aa_only(self):
        ratio = lc.contrast_ratio((0x76, 0x76, 0x76), WHITE)
        self.assertEqual(round(ratio, 2), 4.54)
        self.assertTrue(ratio >= lc.AA_NORMAL)
        self.assertFalse(ratio >= lc.AAA_NORMAL)

    def test_threshold_table(self):
        self.assertEqual(lc.threshold("AA"), 4.5)
        self.assertEqual(lc.threshold("AA", large=True), 3.0)
        self.assertEqual(lc.threshold("AAA"), 7.0)
        self.assertEqual(lc.threshold("AAA", large=True), 4.5)


class FixTests(unittest.TestCase):
    def test_passing_colour_is_returned_unchanged(self):
        self.assertEqual(lc.nearest_passing(BLACK, WHITE, 4.5), (BLACK, 0.0))

    def test_failing_pale_text_is_darkened_to_pass(self):
        pale = lc.parse_colour("#c7d2fe")
        shade, signed = lc.nearest_passing(pale, WHITE, 3.0)
        self.assertLess(signed, 0)
        self.assertGreaterEqual(lc.contrast_ratio(shade, WHITE), 3.0)

    def test_impossible_target_returns_none(self):
        # Against mid-grey #808080 no grey reaches 7:1 (black tops out near 5.3:1).
        grey = (0x77, 0x77, 0x77)
        self.assertIsNone(lc.nearest_passing(grey, (0x80, 0x80, 0x80), 7.0))


class SimulationTests(unittest.TestCase):
    def test_greys_stay_grey_under_each_deficiency(self):
        grey = (128, 128, 128)
        for kind in lc.CVD_MATRICES:
            with self.subTest(kind=kind):
                simulated = lc.simulate(grey, kind)
                for channel in simulated:
                    self.assertLessEqual(abs(channel - 128), 1)

    def test_red_changes_under_protanopia(self):
        self.assertNotEqual(lc.simulate((255, 0, 0), "protanopia"), (255, 0, 0))


class PaletteParsingTests(unittest.TestCase):
    def test_colours_and_pairs_are_read(self):
        text = "ink = #111\npaper = #fff\n\npair ink paper large aaa\n"
        colours, pairs = lc.parse_palette(text, source="x.palette")
        self.assertEqual(colours["ink"], (0x11, 0x11, 0x11))
        self.assertEqual(colours["paper"], WHITE)
        self.assertEqual(pairs, [("ink", "paper", True, True, 4)])

    def test_errors_are_line_numbered(self):
        text = "ink = #111\nbad name = #fff\nbg = #zzz\nink = #222\npair ink nope\nwhat is this\n"
        with self.assertRaises(lc.PaletteError) as ctx:
            lc.parse_palette(text, source="t.palette")
        message = str(ctx.exception)
        for lineno in range(2, 7):
            self.assertIn(f"t.palette:{lineno}:", message)

    def test_pair_needs_two_colour_names(self):
        with self.assertRaises(lc.PaletteError) as ctx:
            lc.parse_palette("ink = #111\npair ink\n", source="p")
        self.assertIn("p:2:", str(ctx.exception))

    def test_evaluate_pairs_marks_failures(self):
        colours = {"grey": (0x76, 0x76, 0x76), "paper": WHITE}
        pairs = [("grey", "paper", False, False, 1), ("grey", "paper", False, True, 2)]
        results = lc.evaluate_pairs(colours, pairs)
        self.assertTrue(results[0]["pass"])
        self.assertFalse(results[1]["pass"])
        self.assertEqual(results[1]["required"], 7.0)


class CommandTests(unittest.TestCase):
    def test_check_json_report(self):
        code, out, _ = run(["check", "#000", "#fff", "--json"])
        self.assertEqual(code, lc.EXIT_OK)
        report = json.loads(out)
        self.assertEqual(report["ratio"], 21.0)
        self.assertTrue(report["passes"]["AAA_normal"])

    def test_invalid_colour_exits_2_with_message(self):
        code, _, err = run(["check", "nope", "#fff"])
        self.assertEqual(code, lc.EXIT_ERROR)
        self.assertIn("unrecognised colour", err)

    def test_fix_reports_nearest_passing_shade(self):
        code, out, _ = run(["fix", "#8c8c8c", "#ffffff"])
        self.assertEqual(code, lc.EXIT_OK)
        self.assertIn("nearest passing shade", out)

    def test_pairs_gate_fails_on_example_palette(self):
        code, out, _ = run(["pairs", EXAMPLE])
        self.assertEqual(code, lc.EXIT_FAIL)
        self.assertIn("pairs pass", out)

    def test_lint_and_matrix_accept_example_palette(self):
        self.assertEqual(run(["lint", EXAMPLE])[0], lc.EXIT_OK)
        self.assertEqual(run(["matrix", EXAMPLE])[0], lc.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
