import base64
import io
import socket
import threading
import time

import pytest
from PIL import Image, ImageDraw

from label_agent import star
from label_agent.__main__ import Agent
from label_agent.printer import PrinterError

from .test_agent import FakeRedmine, run_agent


def star_status(cover_open=False, paper_empty=False, cutter_error=False, offline=False, etb=0):
    """A TSP100IIILAN's status as it sends it over Ethernet: 9 bytes (header 1 = 0x23), header 2's bit 7 set,
    then a two-byte length of extra data (none)"""
    data = bytearray(9)
    data[0] = 0x23
    data[1] = 0x86
    data[2] = (0x20 if cover_open else 0) | (0x08 if offline or cover_open or paper_empty else 0)
    data[3] = 0x08 if cutter_error else 0
    data[5] = 0x08 if paper_empty else 0
    data[7] = ((etb & 0x07) << 1) | ((etb & 0x18) << 2)
    return bytes(data) + b'\x00\x00'


class FakeStarPrinter:
    """A Star receipt printer on TCP: answers ESC ACK SOH, and counts ETBs once the job before them is in"""

    def __init__(self, answers_status=True, unasked=False, **status):
        self.answers_status = answers_status
        self.unasked = unasked  # sends its status as soon as a host connects (ASB)
        self.status = status
        self.etb = 5
        self.received = []
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _send_status(self, conn):
        if self.answers_status:
            conn.sendall(star_status(etb=self.etb, **self.status))

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(1.0)
                if self.unasked:
                    self._send_status(conn)
                data = b''
                while True:
                    try:
                        chunk = conn.recv(65536)
                    except (socket.timeout, OSError):
                        break
                    if not chunk:
                        break
                    data += chunk
                    if chunk.endswith(star.STATUS_REQUEST):
                        self._send_status(conn)
                    if chunk.endswith(star.ETB):
                        self.etb = (self.etb + 1) % 32
                        self._send_status(conn)
                self.received.append(data)

    def last_received(self, seconds=3):
        """What the last connection sent (recorded once it closes)"""
        deadline = time.monotonic() + seconds
        while not self.received and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.received[-1] if self.received else b''

    def close(self):
        self.server.close()


@pytest.fixture
def fake_star():
    printers = []

    def make(**kwargs):
        printer = FakeStarPrinter(**kwargs)
        printers.append(printer)
        return printer

    yield make
    for printer in printers:
        printer.close()


def picture(width=576, height=40):
    image = Image.new('L', (width, height), 255)
    ImageDraw.Draw(image).rectangle((0, 0, 7, height - 1), fill=0)  # first byte of every row black
    return image


def png_base64(image):
    buffer = io.BytesIO()
    image.convert('1').save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue()).decode()


def rows_in(data):
    """The raster rows (b n1 n2 data) in a print job"""
    rows, i = [], 0
    while i < len(data):
        if data[i:i + 3] == b'\x1b*r':
            # ESC * r R/A/B, or a setting ending in NUL
            i = i + 4 if data[i + 3:i + 4] in (b'R', b'A', b'B') else data.index(b'\x00', i) + 1
        elif data[i:i + 2] == b'\x1b\x0c':
            i += 3
        elif data[i:i + 1] == b'b':
            length = data[i + 1] + data[i + 2] * 256
            rows.append(data[i + 3:i + 3 + length])
            i += 3 + length
        elif data[i:i + 1] == star.ETB:
            i += 1
        else:
            raise AssertionError(f'Unexpected byte {data[i]:#x} at {i}')
    return rows


def test_status_header_length():
    assert star.status_length(0x0F) == 7
    assert star.status_length(0x21) == 8
    assert star.status_length(0x23) == 9
    assert star.status_length(0x2F) == 15
    assert star.is_header(0x23)
    assert not star.is_header(0x86)


def test_parse_status():
    status = star.parse_status(star_status(cover_open=True, etb=19))
    assert status['cover_open'] and status['offline']
    assert not status['paper_empty']
    assert status['etb_counter'] == 19
    assert star.describe_status(status) == 'Error: Cover open'
    assert star.describe_status(star.parse_status(star_status(paper_empty=True))) == 'Error: Out of paper'
    assert star.describe_status(star.parse_status(star_status(offline=True))) == 'Error: Offline'
    assert star.describe_status(star.parse_status(star_status())) == 'Ready'
    assert star.describe_status(None) == 'Connected'


def test_raster_prints_rows_and_cuts():
    data = star.raster([picture()], cut='full')
    assert data.startswith(b'\x1b*rR\x1b*rA')
    assert b'\x1b*rF9\x00' in data  # FF mode: feed and full cut
    assert data.endswith(b'\x1b\x0c\x00\x1b*rB')
    rows = rows_in(data)
    assert len(rows) == 40
    assert rows[0] == b'\xff'  # the black first byte; the white rest is left off
    assert b'\x1b*rF13\x00' in star.raster([picture()], cut='partial')


def test_narrow_pictures_are_centered():
    rows = star.to_rows(picture(width=560, height=2))
    assert len(rows[0]) == 72
    assert rows[0][1] == 0xFF  # 8 dots in from the left edge


def test_status_check(fake_star):
    printer = fake_star(paper_empty=True)
    assert star.StarPrinter('127.0.0.1', printer.port).check() == 'Error: Out of paper'
    quiet = fake_star(answers_status=False)
    assert star.StarPrinter('127.0.0.1', quiet.port).check() == 'Connected'


def test_print_waits_for_etb(fake_star):
    printer = fake_star(unasked=True)
    assert star.StarPrinter('127.0.0.1', printer.port).print([picture(), picture()]) == 'printed'
    assert printer.etb == 6
    job = printer.last_received()
    assert job.endswith(star.ETB)
    assert job.count(b'\x1b\x0c\x00') == 2  # each copy cut off


def test_print_without_status_is_sent(fake_star):
    printer = fake_star(answers_status=False)
    assert star.StarPrinter('127.0.0.1', printer.port).print([picture()], timeout=2) == 'sent'


def test_print_refuses_when_the_cover_is_open(fake_star):
    printer = fake_star(cover_open=True)
    with pytest.raises(PrinterError, match='Cover open'):
        star.StarPrinter('127.0.0.1', printer.port).print([picture()])
    received = printer.last_received()
    assert received and b'\x1b*rA' not in received


def test_agent_prints_pictures_on_a_star(fake_star):
    printer = fake_star()
    redmine = FakeRedmine([
        {'id': 3, 'image': png_base64(picture()), 'copies': 2},
        {'id': 4, 'lines': ['SMITH', 'E1234'], 'copies': 1},
    ])
    agent = Agent({'REDMINE_URL': redmine.url, 'AGENT_TOKEN': 'qbl_test', 'PRINTER_TYPE': 'star',
                   'PRINTER': f'tcp://127.0.0.1:{printer.port}'})
    run_agent(agent, lambda: len(redmine.results) == 2)
    redmine.close()

    assert redmine.results[3] == {'status': 'printed'}
    assert redmine.results[4]['status'] == 'failed'
    assert 'receipt printer' in redmine.results[4]['error']
    assert redmine.polls[0]['agent']['printer_model'] == 'TSP143IIILAN'
    assert redmine.polls[0]['agent']['printer_status'] == 'Ready'


def test_label_printers_refuse_pictures(fake_printer):
    printer = fake_printer()
    redmine = FakeRedmine([{'id': 5, 'image': png_base64(picture()), 'copies': 1}])
    agent = Agent({'REDMINE_URL': redmine.url, 'AGENT_TOKEN': 'qbl_test',
                   'PRINTER': f'tcp://127.0.0.1:{printer.port}'})
    run_agent(agent, lambda: 5 in redmine.results)
    redmine.close()
    assert 'receipt printer' in redmine.results[5]['error']


def test_star_needs_an_address_and_a_known_cut():
    with pytest.raises(ValueError, match='PRINTER'):
        Agent({'REDMINE_URL': 'http://x', 'AGENT_TOKEN': 'qbl_test', 'PRINTER_TYPE': 'star'})
    with pytest.raises(ValueError, match='CUT'):
        Agent({'REDMINE_URL': 'http://x', 'AGENT_TOKEN': 'qbl_test', 'PRINTER_TYPE': 'star',
               'PRINTER': 'tcp://127.0.0.1', 'CUT': 'sideways'})
