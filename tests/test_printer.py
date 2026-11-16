import pytest

from label_agent.label import render
from label_agent.printer import (Printer, PrinterError, PrinterUnreachable, describe_status, parse_address)
from tests.conftest import status_bytes


def label():
    return render(['SMITH', 'E1234'])


def test_parse_address():
    assert parse_address('tcp://192.168.0.100') == ('192.168.0.100', 9100)
    assert parse_address('192.168.0.100:9101') == ('192.168.0.100', 9101)


def test_raster_is_for_62mm_continuous_and_cuts():
    data = Printer('127.0.0.1').raster([label()])
    assert data.startswith(b'\x1bia\x01')  # raster mode
    assert b'\x1biz' in data            # media: 62mm
    assert data.endswith(b'\x1a')


def test_status_describes_media(fake_printer):
    printer = fake_printer()
    status = Printer('127.0.0.1', printer.port).status()
    assert describe_status(status) == 'Ready, 62mm continuous'


def test_prints_and_waits_for_the_printer(fake_printer):
    printer = fake_printer(after_print=[status_bytes(0x06, 0x01), status_bytes(0x01), status_bytes(0x01)])
    outcome = Printer('127.0.0.1', printer.port).print([label(), label()], timeout=5)
    assert outcome == 'printed'
    assert printer.received and b'\x1b\x69\x53' in printer.received[-1]


def test_printer_without_network_status_is_sent(fake_printer):
    printer = fake_printer(answers_status=False)
    assert Printer('127.0.0.1', printer.port).print([label()], timeout=3) == 'sent'


def test_printer_error_before_printing(fake_printer):
    printer = fake_printer(status=status_bytes(errors1=0x01))  # no media
    with pytest.raises(PrinterError) as error:
        Printer('127.0.0.1', printer.port).print([label()], timeout=3)
    assert 'Error' in str(error.value)


def test_printer_error_while_printing(fake_printer):
    printer = fake_printer(after_print=[status_bytes(0x02, errors2=0x10)])  # cover open
    with pytest.raises(PrinterError):
        Printer('127.0.0.1', printer.port).print([label()], timeout=3)


def test_unreachable_printer():
    with pytest.raises(PrinterUnreachable):
        Printer('127.0.0.1', 1).status()
