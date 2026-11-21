"""Prints pictures on a Star TSP100III receipt printer (e.g. TSP143IIILAN) over raw TCP (port 9100).

The TSP100 series only prints pictures ("STAR Graphic Mode", raster), so receipts come ready-drawn: a black and
white picture 576 dots wide (72mm at 203 dpi), printed row by row and cut off.

From Star's "STAR Graphic Mode Command Specifications" Rev. 2.32:
  ESC * r R / ESC * r A / ESC * r B   initialize raster mode / enter it / quit it
  ESC * r Q n NUL                     print quality (0: high speed)
  ESC * r P n NUL                     page length (0: continuous)
  ESC * r E n NUL, ESC * r F n NUL    what ESC FF EOT / ESC FF NUL do: 1 nothing, 9 feed + full cut,
                                      13 feed + partial cut (n in ASCII digits)
  b n1 n2 d1...dk                     one row of dots, k = n1 + n2 * 256 bytes, most significant bit leftmost
  ESC FF NUL                          print, then FF mode (feed and cut)
  ETB                                 once everything before it has printed, counts up the status' ETB counter
  ESC ACK SOH                         asks for the status (ASB)

Status (Appendix 1 and 2): header 1 gives the length (bits 1-3 and 5), and over Ethernet header 2's bit 7 says
two more bytes follow giving the length of any extra data after the status.
"""

import logging
import socket
import time

from PIL import Image

from .printer import PrinterError, PrinterUnreachable

log = logging.getLogger(__name__)

PORT = 9100
WIDTH = 576                 # dots across the paper (72mm print area)
ROW_BYTES = WIDTH // 8

ESC = b'\x1b'
STATUS_REQUEST = ESC + b'\x06\x01'
ETB = b'\x17'
CUTS = {'full': 9, 'partial': 13, 'none': 2}   # FF mode: feed, then cut (2: feed only)


def _setting(command, value):
    return ESC + b'*r' + command + str(value).encode('ascii') + b'\x00'


def status_length(header):
    """The status' length in bytes, from its first byte (header 1: bits 1-3 and bit 5)"""
    return ((header >> 1) & 0x07) | ((header >> 2) & 0x08)


def is_header(byte):
    # Header 1: bit 0 set, bits 4 and 7 clear (the other status bytes have bit 0 clear)
    return byte & 0x91 == 0x01


def parse_status(data):
    """A status message's flags, or None if it's too short"""
    if len(data) < 6:
        return None
    status = {
        'offline': bool(data[2] & 0x08),
        'cover_open': bool(data[2] & 0x20),
        'etb_done': bool(data[2] & 0x02),
        'cutter_error': bool(data[3] & 0x08),
        'unrecoverable': bool(data[3] & 0x20),
        'too_hot': bool(data[3] & 0x40),
        'paper_empty': bool(data[5] & 0x08),
        'etb_counter': None,
    }
    if len(data) >= 8:
        # ETB counter bits 0-2 are in bits 1-3, bits 3-4 in bits 5-6
        status['etb_counter'] = ((data[7] >> 1) & 0x07) | ((data[7] >> 2) & 0x18)
    return status


def errors(status):
    """What's stopping the printer, e.g. ['Cover open']"""
    if not status:
        return []
    found = [text for key, text in (('cover_open', 'Cover open'), ('paper_empty', 'Out of paper'),
                                    ('cutter_error', 'Cutter error'), ('too_hot', 'Print head too hot'),
                                    ('unrecoverable', 'Printer error')) if status[key]]
    if status['offline'] and not found:
        found.append('Offline')
    return found


def describe_status(status):
    if not status:
        return 'Connected'  # it didn't answer the status request
    problems = errors(status)
    return 'Error: ' + ', '.join(problems) if problems else 'Ready'


def to_rows(image):
    """The picture as rows of WIDTH dots (1 = black), wider pictures cut and narrower ones centered"""
    gray = image.convert('L')
    if gray.width != WIDTH:
        canvas = Image.new('L', (WIDTH, gray.height), 255)
        canvas.paste(gray, ((WIDTH - gray.width) // 2, 0))
        gray = canvas
    bits = gray.point(lambda value: 255 if value < 128 else 0).convert('1', dither=Image.Dither.NONE)
    data = bits.tobytes()
    return [data[i:i + ROW_BYTES] for i in range(0, len(data), ROW_BYTES)]


def raster(images, cut='full'):
    """The commands that print each picture and cut it off"""
    out = bytearray()
    for image in images:
        out += ESC + b'*rR' + ESC + b'*rA'
        out += _setting(b'Q', 0) + _setting(b'P', 0) + _setting(b'E', 1) + _setting(b'F', CUTS[cut])
        rows = to_rows(image) or [b'\x00']
        for row in rows:
            row = row.rstrip(b'\x00') or b'\x00'  # trailing white bytes aren't needed
            out += b'b' + bytes((len(row) & 0xFF, len(row) >> 8)) + row
        out += ESC + b'\x0c\x00'   # print, feed and cut (FF mode)
        out += ESC + b'*rB'
    return bytes(out)


class StarPrinter:
    def __init__(self, host, port=PORT, cut='full'):
        if cut not in CUTS:
            raise ValueError(f'CUT must be one of {", ".join(CUTS)}')
        self.host = host
        self.port = port
        self.cut = cut

    @property
    def address(self):
        return f'{self.host}:{self.port}'

    def _connect(self, timeout=5.0):
        try:
            return socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as error:
            raise PrinterUnreachable(f"Can't reach the printer at {self.address}: {error}") from error

    @staticmethod
    def _read_exactly(sock, count, deadline):
        data = b''
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(count - len(data))
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            data += chunk
        return data

    def _read_status(self, sock, timeout):
        """The printer's next status message (parsed), or None if none comes in time"""
        deadline = time.monotonic() + timeout
        while True:
            first = self._read_exactly(sock, 1, deadline)
            if first is None:
                return None
            if not is_header(first[0]):
                continue  # not the start of a status: skip it
            rest = self._read_exactly(sock, max(status_length(first[0]) - 1, 1), deadline)
            if rest is None:
                return None
            data = first + rest
            if data[1] & 0x80:
                # Over Ethernet: two bytes give the length of extra status data after it
                length = self._read_exactly(sock, 2, deadline)
                if length is None:
                    return None
                extra = int.from_bytes(length, 'big')
                if extra and self._read_exactly(sock, extra, deadline) is None:
                    return None
            log.debug('Printer status %s', data.hex())
            return parse_status(data)

    def _latest_status(self, sock, timeout):
        """Asks for the status and returns the last one received (the printer may also send it unasked)"""
        sock.sendall(STATUS_REQUEST)
        status = self._read_status(sock, timeout)
        while status is not None:
            newer = self._read_status(sock, 0.2)
            if newer is None:
                break
            status = newer
        return status

    def status(self, timeout=3.0):
        """The printer's status (parse_status), or None when it doesn't answer. Raises PrinterUnreachable."""
        with self._connect() as sock:
            return self._latest_status(sock, timeout)

    def check(self):
        return describe_status(self.status())

    def print(self, images, timeout=30.0):
        """Prints the pictures, each cut off. Checks the printer first; then, when it answers status requests,
        waits until it says everything printed. Returns 'printed' when it said so, 'sent' when it didn't."""
        instructions = raster(images, self.cut) + ETB
        with self._connect() as sock:
            before = self._latest_status(sock, 3.0)
            if errors(before):
                raise PrinterError(describe_status(before))
            log.debug('Printer status before printing: %s', describe_status(before))

            sock.settimeout(timeout)
            try:
                sock.sendall(instructions)
            except OSError as error:
                raise PrinterError(f'Lost the printer at {self.address} while sending: {error}') from error
            if before is None or before['etb_counter'] is None:
                return 'sent'

            # ETB is done once everything before it has printed: the counter moves on
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                status = self._read_status(sock, 1.0)
                if status is None:
                    try:
                        sock.sendall(STATUS_REQUEST)
                    except OSError:
                        break
                    continue
                if errors(status):
                    raise PrinterError(describe_status(status))
                if status['etb_counter'] != before['etb_counter']:
                    return 'printed'
            return 'sent'
