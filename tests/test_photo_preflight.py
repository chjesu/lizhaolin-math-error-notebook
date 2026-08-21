import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / ".agents"
    / "skills"
    / "math-error-notebook"
    / "scripts"
    / "photo_preflight.py"
)
SPEC = importlib.util.spec_from_file_location("photo_preflight", SCRIPT)
photo_preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(photo_preflight)


class PhotoPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_transparency_resize_remote_route_and_cache(self):
        source = self.root / "transparent.png"
        image = Image.new("RGBA", (3000, 1500), (0, 0, 0, 0))
        image.putpixel((1500, 750), (0, 0, 0, 255))
        image.save(source)

        out_dir = self.root / "preflight"
        first = photo_preflight.prepare_photo_previews(
            [source], self.root, out_dir, max_side=2000, preview_side=1600
        )

        self.assertFalse(first["cache_hit"])
        self.assertTrue(first["remote_visual_review_required"])
        self.assertEqual(first["review_route"], "remote_model_visual_review")
        self.assertEqual(first["pages"], 1)
        with Image.open(first["preview_paths"][0]) as preview:
            self.assertEqual(preview.mode, "RGB")
            self.assertEqual(preview.size, (1600, 800))
            self.assertGreaterEqual(min(preview.getpixel((0, 0))), 245)

        packet = json.loads(Path(first["packet"]).read_text(encoding="utf-8"))
        keys = {
            key.lower()
            for page in packet["pages"]
            for key in page
        }
        self.assertFalse(any("ocr" in key or "vlm" in key for key in keys))
        self.assertFalse(packet["database_modified"])

        second = photo_preflight.prepare_photo_previews(
            [source], self.root, out_dir, max_side=2000, preview_side=1600
        )
        self.assertTrue(second["cache_hit"])

        Path(second["preview_paths"][0]).unlink()
        rebuilt = photo_preflight.prepare_photo_previews(
            [source], self.root, out_dir, max_side=2000, preview_side=1600
        )
        self.assertFalse(rebuilt["cache_hit"])
        self.assertTrue(Path(rebuilt["preview_paths"][0]).is_file())

    def test_changed_source_invalidates_cache(self):
        source = self.root / "page.png"
        Image.new("RGB", (100, 100), "white").save(source)
        out_dir = self.root / "preflight"
        photo_preflight.prepare_photo_previews([source], self.root, out_dir)

        Image.new("RGB", (100, 100), "black").save(source)
        refreshed = photo_preflight.prepare_photo_previews(
            [source], self.root, out_dir
        )

        self.assertFalse(refreshed["cache_hit"])


if __name__ == "__main__":
    unittest.main()
