from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from hdmi_display.acp_receive import wait_acp_receive, qualified_channel
from sr_lut_x4_driver import DriverError


class AcpCompletionTests(unittest.TestCase):
    def test_coherent_completion_requires_matching_bit_and_hwh(self):
        import hashlib
        from pathlib import Path
        import tempfile
        from hdmi_display.acp_receive import qualified_overlay
        with tempfile.TemporaryDirectory() as directory:
            bit = Path(directory)/'test.bit'
            hwh = bit.with_suffix('.hwh')
            bit.write_bytes(b'qualified bit')
            hwh.write_bytes(b'ACP wiring')
            overlay = SimpleNamespace(bitfile_name=str(bit))
            hashes = tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in (bit,hwh))
            with patch('hdmi_display.acp_receive.OVERLAY_SHA256',{hashes}):
                self.assertTrue(qualified_overlay(overlay))
                bit.write_bytes(b'different hardware')
                self.assertFalse(qualified_overlay(overlay))

    def channel(self, status=2, count=8294400):
        mmio = SimpleNamespace(read=Mock(side_effect=lambda address: status if address == 0x34 else count))
        buffer = SimpleNamespace(nbytes=8294400, invalidate=Mock())
        return SimpleNamespace(_offset=0x30, _flush_before=False, _active_buffer=buffer,
                               running=True, _mmio=mmio), buffer

    def test_completes_same_buffer_without_software_invalidate(self):
        channel, buffer = self.channel()
        wait_acp_receive(channel, 1)
        buffer.invalidate.assert_not_called()
        self.assertEqual(channel.transferred, buffer.nbytes)
        self.assertIsNone(channel._active_buffer)

    def test_errors_preserve_buffer_for_owned_recovery(self):
        for status in (1, 0x12, 0x22, 0x42):
            channel, buffer = self.channel(status)
            with self.assertRaises(DriverError):
                wait_acp_receive(channel, 1)
            self.assertIs(channel._active_buffer, buffer)

    def test_bad_length_and_wrong_channel_are_rejected(self):
        channel, buffer = self.channel(count=4)
        with self.assertRaises(DriverError):
            wait_acp_receive(channel, 1)
        self.assertIs(channel._active_buffer, buffer)
        channel._offset = 0
        with self.assertRaises(DriverError):
            wait_acp_receive(channel, 1)

    def test_timeout_keeps_ownership(self):
        channel, buffer = self.channel(status=0)
        clock = iter((0, 0, 2))
        with self.assertRaises(TimeoutError):
            wait_acp_receive(channel, 1, clock=lambda: next(clock), sleep=lambda _: None)
        self.assertIs(channel._active_buffer, buffer)

    def test_unknown_driver_uses_existing_completion(self):
        channel, _ = self.channel()
        self.assertFalse(qualified_channel(channel))


if __name__ == '__main__':
    unittest.main()
