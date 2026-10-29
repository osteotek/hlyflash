"""Exercise real Linux termios/pyserial using a pseudo-terminal, without USB."""
import json
import os
import select
import sys
import threading
import unittest

from hlyflash.serialinfo import _JSONStream, query


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux termios integration')
class LinuxSerialIntegrationTests(unittest.TestCase):
    def test_normal_mode_json_over_a_real_linux_serial_port(self):
        import pty
        master, slave = pty.openpty()
        stop = threading.Event()
        errors = []
        values = {'model': 'Free 2', 'firmware': 'V0.22'}
        def emulate_device():
            stream = _JSONStream()
            try:
                while not stop.is_set():
                    if not select.select([master], [], [], 0.05)[0]:
                        continue
                    for request in stream.feed(os.read(master, 1024)):
                        field = request.get('t')
                        if request.get('o') == 'get' and field in values:
                            response = {'o': 'getresult', 't': field, 'v': values[field]}
                            os.write(master, json.dumps(response).encode())
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=emulate_device, daemon=True)
        worker.start()
        try:
            self.assertEqual(query(os.ttyname(slave), fields=tuple(values), timeout=0.8, rounds=1), values)
            self.assertEqual(errors, [])
        finally:
            stop.set()
            worker.join(timeout=2)
            os.close(slave)
            os.close(master)
