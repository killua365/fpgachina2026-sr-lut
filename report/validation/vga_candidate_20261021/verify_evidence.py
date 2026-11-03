"""Recompute the VGA test counts from archived CRC-valid UART frames."""
import binascii
import hashlib
import json
from pathlib import Path
import struct
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def words(raw,identity):
    start=0
    while True:
        start=raw.find(b'SRB5',start)
        if start<0:return
        packet=raw[start:start+381]
        start+=4
        if len(packet)!=381 or packet[4:7]!=bytes((1,112,1)):
            continue
        if binascii.crc_hqx(packet[:-2],0xffff)!=int.from_bytes(packet[-2:],'little'):
            continue
        payload=struct.unpack('<92I',packet[11:-2])
        if payload[0]==identity:yield payload


def main():
    summary=json.loads((HERE/'summary.json').read_text())
    archive=HERE/summary['evidence_archive']
    assert sha(archive.read_bytes())==summary['evidence_sha256']
    with zipfile.ZipFile(archive) as source:
        assert source.testzip() is None
        for item in json.loads(source.read('index.json'))['files']:
            data=source.read(item['path'])
            assert len(data)==item['bytes'] and sha(data)==item['sha256'],item['path']
        for name,expected in summary['cases'].items():
            rows=list(words(source.read(name+'/uart.bin'),int(expected['candidate_id'],16)))
            assert len(rows)==expected['received_valid_crc_records']
            assert max(w[25] for w in rows)==expected['max_rx_good']
            assert max(w[26] for w in rows)==expected['max_rx_bad']
            result=json.loads(source.read(name+'/result.json'))
            assert result['vga_path_passed']==expected['vga_path_passed']
            if expected['vga_path_passed']:
                observation=json.loads(source.read(name+'/finish.request'))
                assert observation['run_id']==result['run_id'] and observation['authority']=='user'
                assert observation['display_normal'] is True
                assert result['configuration_recovery_verified'] and result['source_stop_verified']
                assert not result['failures']
    assert sha((ROOT/summary['candidate_bit']).read_bytes())==summary['candidate_bit_sha256']
    assert sha((ROOT/'build/artifacts/nexys/bit/pattern.bit').read_bytes())==summary['default_bit_sha256']
    print(json.dumps(dict(passed=True,archived_runs=3,final_good_hdmi_frames=summary['cases']['final_candidate']['max_rx_good'],final_bad_hdmi_frames=summary['cases']['final_candidate']['max_rx_bad'],user_observation_bound=True,default_bit_unchanged=True)))


if __name__=='__main__':main()
