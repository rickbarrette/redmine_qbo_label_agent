"""The print agent: asks Redmine for labels every couple of seconds and prints them.

Settings (environment):
  REDMINE_URL     Redmine's address, e.g. https://redmine.example.com (required)
  AGENT_TOKEN     the printer's token from Administration » Label printers (required)
  PRINTER         the printer, e.g. tcp://192.168.0.100 (optional: found on the network when not set)
  PRINTER_MODEL   default QL-820NWB
  LABEL           brother_ql label size, default 62 (62mm continuous)
  LOG_LEVEL       DEBUG, INFO (default) or WARNING
  VERIFY_SSL      0 to skip checking Redmine's certificate (default 1)
"""

import logging
import os
import signal
import sys
import time

import requests

from . import __version__
from .label import render
from .printer import Printer, PrinterError, PrinterUnreachable, describe_status, discover, parse_address
from .redmine import Redmine, Unauthorized

log = logging.getLogger('label_agent')

STATUS_EVERY = 60       # seconds between printer status checks (and reports to Redmine)
DISCOVER_EVERY = 30     # seconds between network searches while the printer isn't found
MAX_BACKOFF = 60        # seconds, when Redmine can't be reached


class Agent:
    def __init__(self, env):
        self.redmine = Redmine(env['REDMINE_URL'], env['AGENT_TOKEN'],
                               verify=env.get('VERIFY_SSL', '1') not in ('0', 'false', 'no'))
        self.model = env.get('PRINTER_MODEL') or 'QL-820NWB'
        self.label = env.get('LABEL') or '62'
        self.fixed_printer = env.get('PRINTER') or None
        self.printer = None
        self.found_model = None
        self.printer_status = 'Not found on the network yet'
        self.reported = None
        self.next_status_at = 0.0
        self.next_discover_at = 0.0
        self.running = True

    # Printer

    def find_printer(self, force=False):
        """The printer: the one set in PRINTER, or the one found on the network (searched again when forced)"""
        if self.fixed_printer:
            if not self.printer:
                host, port = parse_address(self.fixed_printer)
                self.printer = Printer(host, port, self.model, self.label)
            return self.printer
        if self.printer and not force:
            return self.printer
        if not force and time.monotonic() < self.next_discover_at:
            return None

        self.next_discover_at = time.monotonic() + DISCOVER_EVERY
        log.info('Looking for a %s on the network...', self.model)
        try:
            found = discover(self.model)
        except Exception as error:
            log.warning('Network search failed: %s', error)
            found = []
        if not found:
            log.warning('No Brother QL printer found on the network (set PRINTER to give its address)')
            self.printer = None
            self.printer_status = 'Not found on the network'
            return None
        name, host, port = found[0]
        if not self.printer or self.printer.host != host:
            log.info('Found %s at %s:%s', name, host, port)
        self.printer = Printer(host, port, self.model, self.label)
        self.found_model = name
        return self.printer

    def check_printer(self):
        printer = self.find_printer()
        if not printer:
            return
        try:
            self.printer_status = describe_status(printer.status())
        except PrinterUnreachable as error:
            log.warning('%s', error)
            self.printer_status = 'Not reachable'
            if not self.fixed_printer:
                self.find_printer(force=True)  # it may have a new address
        log.debug('Printer %s: %s', printer.address, self.printer_status)

    def agent_info(self):
        """What Redmine shows about this agent, sent when it changes and every minute"""
        printer = self.printer
        return {
            'version': __version__,
            'printer_model': self.found_model or self.model,
            'printer_address': printer.address if printer else '',
            'printer_status': self.printer_status,
        }

    # Labels

    def print_job(self, job):
        lines = job.get('lines') or []
        log.info('Printing label %s: %s (x%s)', job['id'], ' / '.join(lines), job.get('copies', 1))
        try:
            image = render(lines, job.get('min_length_mm', 25.4), job.get('max_length_mm', 101.6))
            copies = max(1, min(int(job.get('copies') or 1), 10))
            printer = self.find_printer() or self.find_printer(force=True)
            if not printer:
                raise PrinterError('No label printer found on the network')
            try:
                outcome = printer.print([image] * copies)
            except PrinterUnreachable:
                if self.fixed_printer:
                    raise
                # Maybe it has a new address: search again and try once more
                printer = self.find_printer(force=True)
                if not printer:
                    raise
                outcome = printer.print([image] * copies)
            log.info('Label %s %s', job['id'], outcome)
            self.printer_status = 'Ready'
            self.send_result(job['id'], True)
        except Exception as error:
            log.error('Label %s failed: %s', job['id'], error)
            self.send_result(job['id'], False, error)

    def send_result(self, job_id, printed, error=None):
        for attempt in range(5):
            try:
                self.redmine.report(job_id, printed, error)
                return
            except Unauthorized:
                raise
            except Exception as report_error:
                log.warning('Telling Redmine about label %s failed (%s), retrying', job_id, report_error)
                time.sleep(2 * (attempt + 1))
        log.error('Gave up telling Redmine about label %s', job_id)

    # Main loop

    def run(self):
        log.info('Label agent %s for %s', __version__, self.redmine.url)
        wait = 2.0
        failures = 0
        while self.running:
            now = time.monotonic()
            if now >= self.next_status_at:
                self.next_status_at = now + STATUS_EVERY
                self.check_printer()
                self.reported = None  # report every minute even without changes

            info = self.agent_info()
            try:
                job, wait = self.redmine.poll(info if info != self.reported else None)
                self.reported = info
                if failures:
                    log.info('Reaching Redmine again')
                failures = 0
                if job:
                    self.print_job(job)
                    continue  # there may be more waiting
                log.debug('No labels')
            except Unauthorized as error:
                log.error('%s: check AGENT_TOKEN (Administration » Label printers)', error)
                wait = MAX_BACKOFF
            except requests.RequestException as error:
                failures += 1
                wait = min(MAX_BACKOFF, 2 ** min(failures, 6))
                (log.warning if failures in (1, 5) or failures % 30 == 0 else log.debug)(
                    "Can't reach Redmine (%s), trying again in %ss", error, wait)
            self.sleep(wait)

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while self.running and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

    def stop(self, *_):
        log.info('Stopping')
        self.running = False


def main():
    logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO').upper(),
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    # brother_ql logs every byte of status at DEBUG and expected conversion notes at INFO
    logging.getLogger('brother_ql').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    missing = [name for name in ('REDMINE_URL', 'AGENT_TOKEN') if not os.environ.get(name)]
    if missing:
        log.error('Set %s', ' and '.join(missing))
        return 2
    agent = Agent(os.environ)
    signal.signal(signal.SIGTERM, agent.stop)
    signal.signal(signal.SIGINT, agent.stop)
    agent.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
