import binascii,copy,struct,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'board/host'))
from decode_frame_diagnostic import assess,decode

def raw_frame(seq,words):
    raw=b'SRF3'+bytes((1,112))+struct.pack('<I',seq)+struct.pack('<28I',*words)
    return raw+struct.pack('<H',binascii.crc_hqx(raw,0xffff))

def good_rows():
    rows=[]
    for i in range(51):
        w=[0]*28;w[0]=0x26100502;w[1]=0x4e334631;w[23]=0x3b;w[27]=1
        w[3]=w[4]=w[7]=100+3*i;w[5]=w[6]=200+6*i;w[8]=100+3*i
        w[10]=6_000_000;w[11]=99+3*i;w[13]=(100+3*i)*518400;w[14]=(200+6*i)*518400
        w[16]=w[18]=1_000_000_000+10_000_000*i;w[20]=1000+10*i;w[21]=100;w[22]=1
        rows.append(dict(offset=i*124,sequence=i,words=w))
    return dict(frames=rows,errors=[],skipped_bytes=0)

class FrameDiagnosticTests(unittest.TestCase):
    def result(self,p):return assess(p,minimum_seconds=5)
    def test_valid(self):
        r=self.result(good_rows());self.assertTrue(r['telemetry_passed'],r)
        self.assertEqual(r['payload_bytes_per_second'],746496000)
        self.assertEqual(r['latency_p95_upper_bound_ms'],60)
        self.assertFalse(r['n3_passed']);self.assertFalse(r['error_counter_ber_valid'])
    def test_round_trip(self):
        p=good_rows();raw=b''.join(raw_frame(r['sequence'],r['words']) for r in p['frames'])
        self.assertEqual(decode(raw),p)
    def test_crc_truncation_and_noise(self):
        p=good_rows();raw=raw_frame(0,p['frames'][0]['words']);damaged=bytearray(raw);damaged[20]^=1
        for data in (raw[:-1],bytes(damaged),b'noise'+raw):self.assertFalse(self.result(decode(data))['telemetry_passed'])
    def test_unknown_version(self):
        raw=bytearray(raw_frame(0,good_rows()['frames'][0]['words']));raw[4]=2
        self.assertEqual(decode(raw)['errors'][0]['error'],'version_or_length')
    def test_bad_status_fields(self):
        for index,value in ((0,0),(1,0),(2,1),(20,0),(21,10_000_000),(23,0xfb),(27,0),(12,1),(22,2)):
            p=good_rows();p['frames'][25]['words'][index]=value
            with self.subTest(index=index):self.assertFalse(self.result(p)['telemetry_passed'])
    def test_gap_and_time(self):
        for field in ('sequence','ui','root'):
            p=good_rows()
            if field=='sequence':p['frames'][25]['sequence']+=1
            else:p['frames'][25]['words'][16 if field=='ui' else 18]=0
            self.assertFalse(self.result(p)['telemetry_passed'])
    def test_counter_regression(self):
        for index in (3,4,5,6,7,8,13,14):
            p=good_rows();p['frames'][25]['words'][index]=p['frames'][24]['words'][index]-1
            self.assertFalse(self.result(p)['telemetry_passed'])
    def test_bounded_beat_counter_wrap(self):
        p=good_rows()
        for r in p['frames']:
            for k in (13,14):r['words'][k]=(r['words'][k]+0xfc000000)&0xffffffff
        self.assertTrue(self.result(p)['telemetry_passed'])
    def test_drops(self):
        p=good_rows()
        for r in p['frames'][25:]:r['words'][9]=1
        self.assertIn('stable_input_frame_drops',self.result(p)['reasons'])
    def test_zero_and_low_rate_not_success(self):
        p=good_rows()
        for r in p['frames']:
            for k in (3,4,5,6,7,8,13,14):r['words'][k]=0
        self.assertFalse(self.result(p)['telemetry_passed'])
    def test_short_and_inconsistent(self):
        p=good_rows();p['frames']=p['frames'][:3];self.assertFalse(self.result(p)['telemetry_passed'])
        p=good_rows();p['frames'][25]['words'][6]+=1
        self.assertIn('scan_accounting_inconsistent',self.result(p)['reasons'])
    def test_latency_target_separate(self):
        p=good_rows()
        for r in p['frames']:r['words'][10]=12_000_000
        r=self.result(p);self.assertTrue(r['telemetry_passed']);self.assertFalse(r['latency_100ms_target'])

if __name__=='__main__':unittest.main()
