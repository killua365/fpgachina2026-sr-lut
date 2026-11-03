"""Reject corrupt/foreign candidate telemetry before authorizing the UI."""
import binascii
import struct
import unittest
from test_candidate import live_status


def frame(sequence=1,candidate=0x26102101,link=True):
    words=[0]*92
    words[0]=candidate;words[1]=0x4e344231;words[40]=0x53524944
    words[35]=0x3b;words[39]=int(link)
    data=b'SRB5'+bytes((1,112,1))+sequence.to_bytes(4,'little')+struct.pack('<92I',*words)
    return data+binascii.crc_hqx(data,0xffff).to_bytes(2,'little')


class TelemetryTests(unittest.TestCase):
    def test_candidate_packet_survives_factory_prefix_and_incomplete_suffix(self):
        value=live_status(b'factory UART\r\n'+frame()+frame(2)[:60])
        self.assertTrue(value['available']);self.assertTrue(value['link_ready'])

    def test_crc_failure_cannot_start_source(self):
        data=bytearray(frame());data[24]^=1
        self.assertFalse(live_status(data)['available'])

    def test_default_bit_is_not_the_candidate(self):
        self.assertFalse(live_status(frame(candidate=0x26101201))['available'])

    def test_dropped_packet_rejected(self):
        self.assertFalse(live_status(frame(1)+frame(3))['available'])

    def test_untrained_link_remains_untrained(self):
        value=live_status(frame(link=False))
        self.assertTrue(value['available']);self.assertFalse(value['link_ready'])


if __name__=='__main__':unittest.main(verbosity=2)
