import pathlib
import shutil
import subprocess
import tempfile
import unittest

import support  # noqa: F401

import extract_thumbnails as E


class NoSipsTests(unittest.TestCase):
    def test_source_has_no_sips(self):
        src = (support.SCRIPTS / "extract_thumbnails.py").read_text(encoding="utf-8")
        self.assertNotIn('"sips"', src)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_resize_jpeg_uses_ffmpeg(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            png, jpg, small = td / "a.png", td / "a.jpg", td / "s.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=1",
                            "-frames:v", "1", str(png)], check=True)
            E.resize_jpeg(png, jpg, 640, 85)
            dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                   str(jpg)], capture_output=True, text=True).stdout.strip()
            self.assertEqual(dims, "640,360")
            # never upscale
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=1",
                            "-frames:v", "1", str(small.with_suffix(".png"))], check=True)
            E.resize_jpeg(small.with_suffix(".png"), small, 640, 85)
            dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                   str(small)], capture_output=True, text=True).stdout.strip()
            self.assertEqual(dims, "320,180")


if __name__ == "__main__":
    unittest.main()
