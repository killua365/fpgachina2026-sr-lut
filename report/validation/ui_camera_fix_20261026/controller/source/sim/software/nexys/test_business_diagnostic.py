"""Synthetic fault injection into complete SRB5 records, with valid CRCs."""
import binascii
import struct
import unittest

from decode_business_diagnostic import assess, assess_identities, decode


def record(index):
    tick, source, scanned, number = (index + 1) * 10_000_000, (index + 1) * 3, (index + 1) * 6, 1 + 2 * index
    w = [0] * 92
    w[0:10] = [0x26101201, 0x4e344231, 0, source, source, scanned, scanned, source, scanned-source, 0]
    w[10], w[11], w[13], w[14], w[16] = 5_000_000, source, source*518400, scanned*518400, tick
    w[18], w[21], w[24], w[25] = 1, source, source, source
    w[27:30] = [1920*1080, 2200, 2200*1125]
    w[30], w[32], w[33], w[35], w[39] = tick, index+1, 100, 0x3b, 1
    s = [0] * 52
    s[:20] = [0x53524944, 9, source, 0, 0, source, scanned, number, scanned-number, 0,
              0, 0, 0, 7, number, 0x402, 4_000_000, 4_000_000, 0, 0]
    for n in range(max(1, number-3), number+1):
        slot = 20 + (n-1) % 4 * 8
        s[slot:slot+8] = [n, 7, n, 0x402, 4_000_000, 0, 9_000_000+(n-1)*5_000_000, 0]
    w[40:] = s
    return dict(sequence=index, offset=index*381, words=w)


def sample():
    return dict(frames=[record(i) for i in range(4)], errors=[], skipped_bytes=0)


def wire(row):
    payload = b'SRB5' + bytes((1, 112, 1)) + struct.pack('<I92I', row['sequence'], *row['words'])
    return payload + struct.pack('<H', binascii.crc_hqx(payload, 0xffff))


class BusinessTelemetryTests(unittest.TestCase):
    def check_bad(self, parsed, reason):
        result = assess_identities(parsed, 2, 4, 7)
        self.assertFalse(result['identity_telemetry_passed'])
        self.assertIn(reason, result['reasons'])

    def test_full_transport_and_bridge_with_independent_events(self):
        parsed = decode(b''.join(wire(r) for r in sample()['frames']))
        result = assess(parsed, 2, 4, 7, minimum_seconds=.29, minimum_new_fps=20)
        self.assertTrue(result['telemetry_passed'], result['reasons'])
        self.assertEqual(result['identity']['new_display_fps'], 20)
        self.assertEqual(result['identity']['independently_recovered_events'], 6)
        self.assertEqual(result['identity']['latency_ms']['p95'], 40)
        self.assertEqual(result['bridge']['rates']['new'], 30)
        self.assertFalse(result['n5_passed'] or result['n6_passed'] or result['formal_release'])

    def test_crc_noise_and_truncation_fail(self):
        good = b''.join(wire(r) for r in sample()['frames'])
        broken = bytearray(good); broken[50] ^= 1
        for raw in (bytes(broken), b'x'+good, good[:-1]):
            self.check_bad(decode(raw), 'business_transport_corrupt_or_unframed')

    def test_telemetry_sequence_gap_fails_even_with_all_events(self):
        p = sample(); p['frames'][2]['sequence'] += 1
        self.check_bad(p, 'business_telemetry_gap')

    def test_overwritten_journal_is_not_averaged(self):
        p = sample(); p['frames'] = [record(0), record(3), record(4)]
        self.check_bad(p, 'display_event_journal_gap')

    def test_replay_at_first_interval_is_detected(self):
        p = sample(); p['frames'][1]['words'][40+20+8+2] = 1
        self.check_bad(p, 'display_identity_replay_or_time_regression')

    def test_retained_event_mutation_is_detected(self):
        p = sample(); p['frames'][1]['words'][40+20+4] -= 1
        self.check_bad(p, 'retained_event_mutated')

    def test_generation_mode_scale_and_time_are_bound(self):
        for offset, value, reason in ((13, 8, 'unexpected_business_identity'),
                                     (15, 0x202, 'unexpected_business_identity'),
                                     (20+8+1, 8, 'journal_business_identity_changed'),
                                     (20+8+6, 21_000_000, 'journal_time_invalid')):
            p = sample(); p['frames'][1]['words'][40+offset] = value
            self.check_bad(p, reason)

    def test_fault_saturation_and_disabled_never_pass(self):
        for flags in (0, 1, 11, 13):
            p = sample(); p['frames'][1]['words'][41] = flags
            self.check_bad(p, 'identity_schema_disabled_saturated_or_faulted')

    def test_control_corrupt_and_partial_scans_are_not_accepted(self):
        for offset, name in ((3,'control_inputs'), (4,'marker_errors'), (9,'identity_errors'),
                             (10,'unmarked_scans'), (11,'incomplete_scans'), (12,'control_scans')):
            p = sample()
            for row in p['frames'][1:]:
                row['words'][40+offset] += 1
            self.check_bad(p, 'stable_business_'+name)

    def test_counter_regression_and_gap_inconsistency(self):
        p = sample(); p['frames'][1]['words'][42] = 0
        self.check_bad(p, 'business_counter_regression_or_rate_valid_inputs')
        p = sample(); p['frames'][-1]['words'][58] = 1
        self.check_bad(p, 'independent_sequence_gap_mismatch')

    def test_requested_fps_is_business_rate(self):
        p = sample()
        result = assess(p, 2, 4, 7, minimum_seconds=.29, minimum_new_fps=25)
        self.assertIn('new_display_rate_below_requested_target', result['reasons'])
        self.assertEqual(result['bridge']['rates']['new'], 30)

    def test_static_image_keeps_older_mode_events_without_counting_them(self):
        # A mode change leaves earlier journal slots intact. A static image may
        # never overwrite them, so only events INSIDE the window need its mode.
        p = sample()
        base = record(3)['words'][40:]
        base[15] = 0x401
        for n in (4, 5, 6):
            start = 20 + (n-1) % 4 * 8
            base[start+1], base[start+3] = 6, 0x403
        base[20+(7-1)%4*8+3] = 0x401
        p['frames'] = [record(i) for i in range(3, 7)]
        for i, row in enumerate(p['frames']):
            row['words'][40:] = base[:]
            for offset, increment in ((2, 3), (5, 3), (6, 6), (8, 6)):
                row['words'][40+offset] += i*increment
        result = assess_identities(p, 1, 4, 7)
        self.assertTrue(result['identity_telemetry_passed'], result['reasons'])
        self.assertEqual(result['new_display_fps'], 0)
        self.assertEqual(result['independently_recovered_events'], 0)

    def test_bridge_loss_is_not_hidden_by_good_identity(self):
        p = sample(); p['frames'][-1]['words'][19] += 1
        result = assess(p, 2, 4, 7, minimum_seconds=.29)
        self.assertTrue(result['identity']['identity_telemetry_passed'])
        self.assertIn('input_loss_or_overflow', result['reasons'])


if __name__ == '__main__':
    unittest.main()
