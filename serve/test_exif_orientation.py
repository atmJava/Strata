"""#1229: a phone JPEG stored turned (EXIF Orientation 6 or 8) is turned upright before the vision encoder reads it."""
import io
import unittest
from unittest import mock

from serve.server import Vision

try:
    from PIL import Image
except ImportError:                 # Pillow is optional: the image then goes through as it was
    Image = None


def jpeg(size, orientation=None) -> bytes:
    im = Image.new("RGB", size, (200, 30, 30))
    try:
        exif = Image.Exif()
        if orientation is not None:
            exif[0x0112] = orientation
        out = io.BytesIO()
        im.save(out, format="JPEG", exif=exif)
        return out.getvalue()
    finally:
        im.close()


@unittest.skipIf(Image is None, "Pillow is not installed")
class ExifOrientation(unittest.TestCase):
    def test_a_turned_jpeg_comes_out_upright_as_a_png(self):
        original_close = Image.Image.close
        closes = 0

        def count_close(image):
            nonlocal closes
            closes += 1
            original_close(image)

        for tag in (6, 8):
            data = jpeg((40, 20), tag)
            with mock.patch.object(Image.Image, "close", count_close):
                out = Vision.normalize(data)
            self.assertEqual(out[:8], b"\x89PNG\r\n\x1a\n")
            with Image.open(io.BytesIO(out)) as image:
                self.assertEqual(image.size, (20, 40))
        self.assertGreaterEqual(closes, 2, "normalize left the rotated Pillow image open")

    def test_a_jpeg_without_the_tag_or_with_tag_1_passes_through_unchanged(self):
        for tag in (None, 1):
            data = jpeg((40, 20), tag)
            self.assertIs(Vision.normalize(data), data)

    def test_png_passes_through(self):
        out = io.BytesIO()
        image = Image.new("RGB", (4, 4))
        try:
            image.save(out, format="PNG")
        finally:
            image.close()
        self.assertEqual(Vision.normalize(out.getvalue()), out.getvalue())

    def test_a_transparent_tiff_is_flattened_and_closed(self):
        image = Image.new("RGBA", (3, 2), (10, 20, 30, 0))
        try:
            with io.BytesIO() as source:
                image.save(source, format="TIFF")
                data = source.getvalue()
        finally:
            image.close()
        out = Vision.normalize(data)
        with Image.open(io.BytesIO(out)) as converted:
            self.assertEqual(converted.mode, "RGB")
            self.assertEqual(converted.size, (3, 2))
            self.assertEqual(converted.getpixel((0, 0)), (255, 255, 255))

    def test_a_broken_jpeg_is_left_as_it_was_sent(self):
        data = b"\xff\xd8\xff" + b"not a jpeg"
        self.assertEqual(Vision.normalize(data), data)


if __name__ == "__main__":
    unittest.main()
