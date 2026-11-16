import socket
import threading

import pytest
from brother_ql.models import ModelsManager


def status_bytes(status_type=0x00, phase=0x00, errors1=0, errors2=0, width=62, media=0x0A):
    """A Brother QL status message (32 bytes) as a QL-820NWB sends it"""
    model = next(m for m in ModelsManager().iter_elements() if m.identifier == 'QL-820NWB')
    data = bytearray(32)
    data[0:3] = b'\x80\x20\x42'
    data[3] = model.series_code
    data[4] = model.model_code
    data[8] = errors1
    data[9] = errors2
    data[10] = width
    data[11] = media
    data[18] = status_type
    data[19] = phase
    return bytes(data)


class FakePrinter:
    """A label printer on TCP: answers status requests, and after a print job sends the given messages"""

    def __init__(self, answers_status=True, after_print=None, status=None):
        self.answers_status = answers_status
        self.status = status or status_bytes()
        self.after_print = after_print if after_print is not None else [status_bytes(0x01)]
        self.received = []
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(1.0)
                data = b''
                answered = False
                while True:
                    try:
                        chunk = conn.recv(65536)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    data += chunk
                    if self.answers_status and not answered and b'\x1b\x69\x53' in data:
                        conn.sendall(self.status)
                        answered = True
                    # The print command (with feeding) ends a job: 0x1A
                    if data.endswith(b'\x1a'):
                        for message in self.after_print if self.answers_status else []:
                            conn.sendall(message)
                        break
                self.received.append(data)

    def close(self):
        self.server.close()


@pytest.fixture
def fake_printer():
    printers = []

    def make(**kwargs):
        printer = FakePrinter(**kwargs)
        printers.append(printer)
        return printer

    yield make
    for printer in printers:
        printer.close()
