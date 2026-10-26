import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.quant_formats as Q


class Response:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read(self, n=-1):
        if n < 0:
            out, self.data = self.data, b""
            return out
        out, self.data = self.data[:n], self.data[n:]
        return out

    def close(self):
        self.closed = True


class QuantFormatsHTTP(unittest.TestCase):
    def test_range_reads_close_each_response(self):
        responses = [Response(b"abc"), Response(b"def")]
        with mock.patch.object(Q.urllib.request, "urlopen", side_effect=responses) as opened:
            source = Q.RangeFile("https://example.invalid/model.gguf")
            self.assertEqual(source.read(4), b"abcd")
            self.assertTrue(responses[0].closed)
            self.assertEqual(source.read(2), b"ef")
            self.assertTrue(responses[1].closed)
        self.assertEqual(opened.call_count, 2)

    def test_tree_response_closes_before_headers_are_parsed(self):
        tree = json.dumps([{"type": "file", "path": "model-00001.gguf"}]).encode()
        response = Response(tree)
        made = []
        with mock.patch.object(Q.urllib.request, "urlopen", return_value=response), \
                mock.patch.object(Q, "Header", side_effect=lambda name, fh: made.append((name, fh)) or name):
            self.assertEqual(Q.hf_headers("org/model", "main", ""), ["model-00001.gguf"])
        self.assertTrue(response.closed)
        self.assertEqual(made[0][0], "model-00001.gguf")


if __name__ == "__main__":
    unittest.main()
