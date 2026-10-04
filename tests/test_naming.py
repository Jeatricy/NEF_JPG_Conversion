import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

import app


class NamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        register_heif_opener()

    def test_parse_exif_datetime(self) -> None:
        captured_at = app.parse_photo_datetime("2026:01:05 19:38:02")

        self.assertIsNotNone(captured_at)
        self.assertEqual(captured_at.strftime("%Y%m%d%H%M%S"), "20260105193802")

    def test_heic_uses_capture_datetime_for_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.heic"
            exif = Image.Exif()
            exif[36867] = "2026:01:05 19:38:02"
            Image.new("RGB", (8, 8), (120, 80, 40)).save(
                source,
                format="HEIF",
                exif=exif,
            )

            self.assertEqual(app.datetime_name_for_source(source), "20260105193802")

    def test_next_available_path_adds_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            original = output_dir / "20260105193802.jpg"
            original.touch()

            self.assertEqual(
                app.next_available_path(original),
                output_dir / "20260105193802_1.jpg",
            )


if __name__ == "__main__":
    unittest.main()
