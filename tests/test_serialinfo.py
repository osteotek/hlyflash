import unittest
from unittest.mock import patch

from hlyflash.serialinfo import _JSONStream, query


class JSONStreamTests(unittest.TestCase):
    def test_reply_without_newline(self):
        self.assertEqual(list(_JSONStream().feed(
            b'{"o":"getresult","t":"model","v":"Free 3"}'
        )), [{"o": "getresult", "t": "model", "v": "Free 3"}])

    def test_fragmented_and_concatenated_replies(self):
        stream = _JSONStream()
        self.assertEqual(list(stream.feed(b'{"t":"model","v":"Fre')), [])
        self.assertEqual(list(stream.feed(
            b'e 3"}{"t":"battery","v":"100"}\r\n'
        )), [{"t": "model", "v": "Free 3"}, {"t": "battery", "v": "100"}])

    def test_split_utf8(self):
        stream = _JSONStream()
        data = '{"v":"阅"}'.encode()
        self.assertEqual(list(stream.feed(data[:7])), [])
        self.assertEqual(list(stream.feed(data[7:])), [{"v": "阅"}])

    def test_invalid_line_then_reply(self):
        self.assertEqual(list(_JSONStream().feed(
            b'not JSON\n{"t":"model","v":"Free 3"}\n'
        )), [{"t": "model", "v": "Free 3"}])


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.pending = []
        self.closed = False

    def reset_input_buffer(self):
        pass

    def write(self, data):
        # Unrelated notifications must not satisfy a query.
        self.pending.extend([
            b'[]\n{"o":"setresult","t":"model","v":"wrong"}',
            b'{"o":"getresult","t":"model","v":"Fre',
            b'e 3"}',
        ])

    def flush(self):
        pass

    def read(self, size):
        return self.pending.pop(0) if self.pending else b""

    def close(self):
        self.closed = True


class QueryTests(unittest.TestCase):
    def test_matches_only_getresult_and_closes_port(self):
        device = FakeSerial()
        with patch("serial.Serial", return_value=device), patch("time.sleep"):
            self.assertEqual(query("fake", fields=("model",)), {"model": "Free 3"})
        self.assertTrue(device.closed)

    def test_empty_fields_do_not_open_port(self):
        with patch("serial.Serial") as open_port:
            self.assertEqual(query("fake", fields=()), {})
            open_port.assert_not_called()


if __name__ == "__main__":
    unittest.main()
