"""Readback checks remain strict across Windows report encodings."""
from pathlib import Path
import tempfile
import unittest
from ddr_device_status import properties

REPORT = b"""IDCODE_HEX string true 13636093
REGISTER.USR_ACCESS string true 26101201
REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR string true 0
REGISTER.CONFIG_STATUS.BIT12_INIT_B_PIN string true 1
REGISTER.CONFIG_STATUS.BIT14_DONE_PIN string true 1
REGISTER.CONFIG_STATUS.BIT15_IDCODE_ERROR string true 0
REGISTER.CONFIG_STATUS.BIT08_MODE_PIN_M[0] string true 1
REGISTER.CONFIG_STATUS.BIT09_MODE_PIN_M[1] string true 0
REGISTER.CONFIG_STATUS.BIT10_MODE_PIN_M[2] string true 0
"""


class DeviceStatusTests(unittest.TestCase):
    def check_report(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'device.rpt'
            path.write_bytes(data)
            properties(path, '26101201')
            self.assertEqual(path.read_bytes(), data)

    def test_locale_paths_and_line_endings(self):
        for encoding in ('utf-8', 'gbk'):
            for ending in (b'\n', b'\r\n'):
                with self.subTest(encoding=encoding, ending=ending):
                    path = 'PROGRAM.FILE string true D:/FPGA/sr_lut_fpga/design.bit\n'.encode(encoding)
                    self.check_report((path + REPORT).replace(b'\n', ending))

    def test_missing_duplicate_and_wrong_identity_rejected(self):
        for data in (REPORT.replace(b'IDCODE_HEX string true 13636093\n', b''),
                     REPORT + b'REGISTER.USR_ACCESS string true 26101201\n',
                     REPORT.replace(b'26101201', b'00000000'),
                     REPORT.replace(b'BIT00_CRC_ERROR string true 0', b'BIT00_CRC_ERROR string true 1')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.check_report(data)

    def test_broken_or_nonascii_property_rejected(self):
        for value in (b'2610\n1201', b'26101201\xff', b'\n26101201'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check_report(REPORT.replace(b'26101201', value))


if __name__ == '__main__':
    unittest.main()
