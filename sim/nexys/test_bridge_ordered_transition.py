import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'board/host'))
from bridge_ordered_transition import read_transition


class TransitionTests(unittest.TestCase):
    def test_missing_or_partial_marker_does_not_end_candidate_monitoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'marker.txt'
            self.assertIsNone(read_transition(p,None,lambda:100))
            for text in ('','12','120'):
                p.write_text(text)
                self.assertIsNone(read_transition(p,100,lambda:150))

    def test_complete_marker_after_requested_restore_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'marker.txt';p.write_text('120\n')
            self.assertEqual(read_transition(p,100,lambda:150),120)

    def test_early_future_late_and_malformed_markers_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'marker.txt'
            for text,request,now in [('120\n',None,150),('90\n',100,150),
                                     ('160\n',100,150),('120000000101\n',100,120000000200),
                                     ('0\n',0,150),('bad\n',100,150),('120\n121\n',100,150)]:
                with self.subTest(text=text,request=request):
                    p.write_text(text)
                    with self.assertRaises(ValueError):read_transition(p,request,lambda:now)


if __name__=='__main__':unittest.main()
