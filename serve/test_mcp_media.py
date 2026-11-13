"""#2473, opt-in: a picture an MCP tool returns reaches the vision reader as a picture.

`result_text` used to turn every picture block into "[image (mime, N bytes): not shown to the model]",
with no way to say otherwise.  With "mcp.image_results": true in the config the block is handed to the
caller as an image part instead, and the text says the picture follows; a type the reader cannot take,
an audio block, or a picture over the cap keeps the note, and the note now says why.

Off is the default and the text is byte for byte what it was: a setup that never asks for pictures sees
no change.

    python -m serve.test_mcp_media
"""
import unittest

from serve.mcp import DEFAULTS, IMAGE_MAX_BYTES, IMAGE_MIMES, result_text


def picture(mime="image/png", data="iVBORw0KGgo="):
    return {"type": "image", "mimeType": mime, "data": data}


class ResultText(unittest.TestCase):
    def test_off_by_default_and_the_note_is_unchanged(self):
        self.assertIs(DEFAULTS["image_results"], False)
        note = result_text({"content": [picture()]})
        self.assertEqual(note, "[image (image/png, 9 bytes): not shown to the model]")

    def test_a_picture_is_an_image_part_when_asked_for(self):
        images = []
        text = result_text({"content": [{"type": "text", "text": "shot:"}, picture()]}, images)
        self.assertIn("shot:", text)
        self.assertIn("shown to the model below", text)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["type"], "image_url")
        self.assertTrue(images[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_every_type_the_reader_takes(self):
        for mime in IMAGE_MIMES:
            with self.subTest(mime=mime):
                images = []
                result_text({"content": [picture(mime)]}, images)
                self.assertEqual(len(images), 1, mime)

    def test_a_type_it_cannot_take_keeps_the_note_and_says_why(self):
        images = []
        text = result_text({"content": [picture("image/tiff")]}, images)
        self.assertEqual(images, [])
        self.assertIn("not shown to the model", text)
        self.assertIn("PNG, JPEG and WebP", text)

    def test_audio_is_never_a_picture(self):
        images = []
        result_text({"content": [{"type": "audio", "mimeType": "audio/wav", "data": "AAAA"}]}, images)
        self.assertEqual(images, [])

    def test_a_picture_over_the_cap_keeps_the_note(self):
        images = []
        big = "A" * ((IMAGE_MAX_BYTES * 4 // 3) + 4)
        text = result_text({"content": [picture(data=big)]}, images)
        self.assertEqual(images, [])
        self.assertIn("not shown to the model", text)

    def test_an_empty_block_is_not_a_picture(self):
        images = []
        result_text({"content": [picture(data="")]}, images)
        self.assertEqual(images, [])

    def test_text_and_a_resource_still_read_as_before(self):
        text = result_text({"content": [{"type": "text", "text": "hi"},
                                        {"type": "resource", "resource": {"uri": "file:///x", "mimeType": "a/b"}}]})
        self.assertTrue(text.startswith("hi"))
        self.assertIn("not shown", text)


if __name__ == "__main__":
    unittest.main()
