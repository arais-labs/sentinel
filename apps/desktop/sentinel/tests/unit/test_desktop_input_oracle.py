"""The fullscreen/input oracle must not accept partial or misdirected success."""

import ast
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest


class InputOracleTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "fixtures/desktop-contract.py"
        tree = ast.parse(path.read_text())
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name
            in {"capture_sample_positions", "geometry_markers", "geometry_samples", "click"}
        ]
        self.state = {
            "color": (0, 0, 1),
            "clicked": threading.Event(),
            "first_click_position": [],
            "area": SimpleNamespace(grab_focus=lambda: None),
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), self.state)

    def test_interior_only_window_is_not_fullscreen_ready(self):
        for width, height in [(1280, 800), (1920, 1200)]:
            points = self.state["capture_sample_positions"](width, height)

            # This smaller blue window passes the original four quarter samples.
            def inside(x, y):
                return 40 <= x < width - 40 and 40 <= y < height - 40

            self.assertTrue(all(inside(x, y) for x, y in points[:4]))
            self.assertFalse(all(inside(x, y) for x, y in points))
            self.assertIn((100, 100), points)
            self.assertIn((20, 20), points)
            self.assertIn((width - 21, height - 21), points)

    def test_samples_are_in_bounds_and_do_not_intersect_damage_marker(self):
        for width, height in [(1280, 800), (1920, 1200)]:
            for x, y in self.state["capture_sample_positions"](width, height):
                self.assertTrue(0 <= x < width and 0 <= y < height)
                self.assertFalse(4 <= x <= 36 and 4 <= y <= 5)

    def test_first_left_click_position_cannot_be_replaced_by_retry(self):
        click = self.state["click"]
        click(None, SimpleNamespace(button=1, x=88.0, y=85.0))
        click(None, SimpleNamespace(button=1, x=100.0, y=100.0))
        self.assertTrue(self.state["clicked"].is_set())
        self.assertEqual(self.state["first_click_position"], [(88.0, 85.0)])

    def test_compositor_edge_shading_is_not_application_geometry(self):
        width, height = 1280, 800
        shaded = (width // 2, 1)
        self.assertNotIn(shaded, self.state["capture_sample_positions"](width, height))
        self.assertNotIn(
            shaded, [point for point, _ in self.state["geometry_samples"](width, height)]
        )

    def test_fiducials_reject_zoom_and_translation_despite_fullscreen_coverage(self):
        for width, height in [(1280, 800), (1920, 1200)]:
            markers = self.state["geometry_markers"](width, height)

            def pixel(x, y):
                for cx, cy in markers:
                    if cx - 4 <= x < cx + 4 and cy - 4 <= y < cy + 4:
                        return (255, 255, 255)
                    if cx - 6 <= x < cx + 6 and cy - 6 <= y < cy + 6:
                        return (0, 0, 0)
                return (0, 0, 255)

            samples = self.state["geometry_samples"](width, height)
            self.assertTrue(all(pixel(x + 0.5, y + 0.5) == wanted for (x, y), wanted in samples))
            for scale, dx, dy in [(1.01, 0, 0), (1.1, 0, 0), (1, 3, 0), (1, 0, -3)]:

                def source(x, y):
                    return (
                        (x + 0.5 - width / 2 - dx) / scale + width / 2,
                        (y + 0.5 - height - dy) / scale + height,
                    )

                self.assertFalse(
                    all(pixel(*source(x, y)) == wanted for (x, y), wanted in samples),
                    (width, height, scale, dx, dy),
                )
            # Background color checks remain independent of the fiducials.
            self.assertTrue(
                all(
                    pixel(x, y) == (0, 0, 255)
                    for x, y in self.state["capture_sample_positions"](width, height)
                )
            )

    def test_other_button_does_not_satisfy_left_click(self):
        self.state["click"](None, SimpleNamespace(button=3, x=100.0, y=100.0))
        self.assertFalse(self.state["clicked"].is_set())
        self.assertEqual(self.state["first_click_position"], [])


if __name__ == "__main__":
    unittest.main()
