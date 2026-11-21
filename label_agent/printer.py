"""Finds the Brother QL printer on this network and sends it labels over raw TCP (port 9100)."""

import logging
import socket
import threading
import time

from brother_ql.conversion import convert
from brother_ql.raster import BrotherQLRaster
from brother_ql.reader import interpret_response

log = logging.getLogger(__name__)

PORT = 9100
# What Brother printers announce over Bonjour/mDNS
SERVICE_TYPES = ['_pdl-datastream._tcp.local.', '_printer._tcp.local.', '_ipp._tcp.local.']
STATUS_REQUEST = b'\x00' * 200 + b'\x1b\x40' + b'\x1b\x69\x53'   # invalidate, initialize, status request
STATUS_LENGTH = 32


class PrinterError(Exception):
    """The printer couldn't be reached or reported an error"""


class PrinterUnreachable(PrinterError):
    """Nothing answered at the printer's address"""


def parse_address(address):
    """'tcp://192.168.0.100:9100', '192.168.0.100' -> ('192.168.0.100', 9100)"""
    address = address.strip()
    if address.startswith('tcp://'):
        address = address[6:]
    host, _, port = address.rstrip('/').partition(':')
    return host, int(port) if port else PORT


def discover(model, timeout=5.0):
    """Brother QL printers announcing themselves on this network: [(name, host, port)], the given model first"""
    from zeroconf import ServiceBrowser, Zeroconf

    found = {}
    lock = threading.Lock()

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if not info:
                return
            props = {k.decode(errors='ignore').lower(): (v or b'').decode(errors='ignore')
                     for k, v in (info.properties or {}).items()}
            label = props.get('ty') or props.get('product', '').strip('()') or name.split('.')[0]
            if 'QL' not in (label + name).upper():
                return
            addresses = info.parsed_addresses()
            if not addresses:
                return
            port = PORT if type_.startswith('_ipp') or type_.startswith('_printer') else (info.port or PORT)
            with lock:
                found.setdefault(addresses[0], (label.replace('Brother ', '').strip(), addresses[0], port))

        def update_service(self, zc, type_, name):
            pass

        def remove_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    try:
        browsers = [ServiceBrowser(zc, service_type, Listener()) for service_type in SERVICE_TYPES]
        time.sleep(timeout)
        for browser in browsers:
            browser.cancel()
    finally:
        zc.close()

    model = (model or '').upper()
    return sorted(found.values(), key=lambda printer: (model not in printer[0].upper(), printer[1]))


def describe_status(status):
    """A short description of a status response, e.g. 'Ready, 62mm continuous'"""
    if not status:
        return 'Connected'  # it doesn't answer status requests over the network
    if status['errors']:
        return 'Error: ' + ', '.join(status['errors'])
    media = ''
    if status.get('media_width'):
        kind = status.get('media_type') or ''
        kind = 'continuous' if 'continuous' in kind.lower() else ('die-cut' if 'die' in kind.lower() else kind)
        media = f", {status['media_width']}mm {kind}".rstrip()
    return 'Ready' + media


class Printer:
    def __init__(self, host, port=PORT, model='QL-820NWB', label='62'):
        self.host = host
        self.port = port
        self.model = model
        self.label = label

    @property
    def address(self):
        return f'{self.host}:{self.port}'

    def _connect(self, timeout=5.0):
        try:
            return socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as error:
            raise PrinterUnreachable(f"Can't reach the printer at {self.address}: {error}") from error

    def check(self):
        """A short description of the printer's status, e.g. 'Ready, 62mm continuous'"""
        return describe_status(self.status())

    @staticmethod
    def _read_status(sock, timeout):
        """The printer's next 32-byte status message, or None if it sends none in time"""
        sock.settimeout(timeout)
        data = b''
        try:
            while len(data) < STATUS_LENGTH:
                chunk = sock.recv(STATUS_LENGTH - len(data))
                if not chunk:
                    break
                data += chunk
        except (socket.timeout, OSError):
            pass
        if len(data) < STATUS_LENGTH:
            return None
        try:
            return interpret_response(data)
        except Exception as error:  # an unexpected reply: carry on without it
            log.debug('Unreadable printer status %s: %s', data.hex(), error)
            return None

    def status(self, timeout=3.0):
        """The printer's status (brother_ql's interpret_response), or None when it doesn't answer status
        requests over the network. Raises PrinterError when it can't be reached."""
        with self._connect() as sock:
            sock.sendall(STATUS_REQUEST)
            return self._read_status(sock, timeout)

    def raster(self, images):
        qlr = BrotherQLRaster(self.model)
        qlr.exception_on_warning = True
        # Images are drawn the way the label is read (length x 696), so turn them for the printer
        return convert(qlr=qlr, images=images, label=self.label, rotate='90', threshold=70, dither=False,
                       compress=False, red=False, dpi_600=False, hq=True, cut=True)

    def print(self, images, timeout=30.0):
        """Prints the images (one label each). Checks the printer first, then follows its status messages
        when it sends them. Returns 'printed' when the printer said so, 'sent' when it didn't say."""
        instructions = self.raster(images)
        with self._connect() as sock:
            sock.sendall(STATUS_REQUEST)
            before = self._read_status(sock, 3.0)
            if before and before['errors']:
                raise PrinterError(describe_status(before))
            log.debug('Printer status before printing: %s', describe_status(before))

            sock.settimeout(timeout)
            try:
                sock.sendall(instructions)
            except OSError as error:
                raise PrinterError(f'Lost the printer at {self.address} while sending: {error}') from error
            if before is None:
                return 'sent'  # it doesn't answer over the network, so there's nothing to wait for

            deadline = time.monotonic() + timeout
            printed = 0
            while time.monotonic() < deadline:
                status = self._read_status(sock, max(0.1, deadline - time.monotonic()))
                if status is None:
                    break
                log.debug('Printer: %s / %s %s', status['status_type'], status['phase_type'], status['errors'])
                if status['errors'] or status['status_type'] == 'Error occurred':
                    raise PrinterError(describe_status(status))
                if status['status_type'] == 'Printing completed':
                    printed += 1
                    if printed >= len(images):
                        return 'printed'
            return 'printed' if printed else 'sent'
