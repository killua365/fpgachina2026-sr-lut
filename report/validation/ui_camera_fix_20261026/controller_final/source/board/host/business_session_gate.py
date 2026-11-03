"""Separate N5 session qualification; never rewrites the offline BIT evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from business_session_policy import LIMITS, OPERATOR_LIMITS, DEMO_LIMITS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
from archive_store import resolve_payload

HARDWARE = ROOT/'build/artifacts/nexys'
HARDWARE_SHA = '16a336723d8fb7c9355989ade5ad6dfb029d603ac272cda63d76efcb5dcfc5f4'
SOURCE = 'board/release/source.json'
CONTRACT = dict(schema='n5_business_session_v1', candidate='26101201', limits=LIMITS, operator_limits=OPERATOR_LIMITS, demo_limits=DEMO_LIMITS,
                vivado='2026.1', flash_write_allowed=False, display_end_dp_tests=False,
                source_start_order='nexys_first', n5_passed=False, n6_passed=False, formal_release=False)
INPUTS = tuple('board/host/'+name for name in (
    'business_session_gate.py', 'business_session_live.py', 'business_session_policy.py',
    'business_session_remote.py', 'business_session_source.py', 'business_session_progress.py', 'supervise_business_session.py',
    'run_business_session.tcl', 'analyze_business_session.py', 'decode_business_diagnostic.py',
    'decode_bridge_diagnostic.py', 'decode_frame_diagnostic.py', 'bridge_ordered_protocol.py',
    'bridge_ordered_clock.py', 'bridge_ordered_json.py', 'bridge_ordered_transition.py',
    'longrun_hw.tcl', 'longrun_process.py', 'longrun_lease.py', 'longrun_heartbeat.py',
    'longrun_heartbeat.ps1', 'longrun_inventory.ps1', 'capture_bridge_matrix_uart.ps1',
    'capture_ftdi_uart.py', 'test_ftdi_capture.py', 'ftdi_status.py', 'test_ftdi_status.py',
    'recover_hd1080_long.tcl', 'supervise_bridge_reacquire.py', 'bridge_matrix_policy.py', 'ddr_device_status.py', 'test_device_status.py')) + (
    SOURCE, 'board/host/prepare_business_session.py', 'board/host/run_business_session.ps1',
    'sim/software/host/test_business_operator.py', 'board/host/package_n5_operator.py',
    'sim/software/ps/test_hdmi_workspace.py', 'sim/software/ps/test_hdmi_display.py', 'sim/software/ps/test_hdmi_presentation.py',
    'board/host/deploy_business_session.py', 'board/host/resolve_hdmi_python.ps1',
    'board/host/collect_business_session.py', 'board/host/collect_n4_source.py',
    'board/host/audit_hdmi_business_pixels.py', 'sim/software/nexys/test_business_diagnostic.py',
    'board/pynq/hdmi_business_app.py', 'board/host/package_n5_session.py',
    'board/pynq/legacy_service_rollback.py', 'board/pynq/hdmi_m1.py',
    'board/pynq/hdmi_m1_supervisor.py', 'board/pynq/install_bootpy_once.py',
    'sim/software/host/test_bootpy_once.py', 'sim/software/host/test_business_recovery_status.py',
    'sim/software/host/test_business_final_media.py', 'board/host/package_n5_final_media.py',
    'sim/software/ps/test_hdmi_interactive_export.py', 'sim/nexys/test_business_query.py',
    'src/ps/hdmi_display/engine.py', 'src/ps/hdmi_display/frame_trace.py',
    'sim/software/ps/test_hdmi_session_trace.py', 'sim/software/ps/test_hdmi_engine.py', 'sim/software/ps/test_hdmi_runtime.py',
    'sim/nexys/test_business_session.py', 'sim/nexys/test_business_session_loader.py',
    'sim/nexys/test_business_session_supervisor.py',
    'sim/nexys/test_bridge_continuous_supervisor.py', 'sim/nexys/test_bridge_ordered_loader.py',
    'sim/nexys/test_sram_pattern.py', 'sim/nexys/qualify_business_session.py',
    'sim/nexys/test_business_package_binding.py',
    'board/host/business_evidence_files.py', 'sim/nexys/test_business_evidence_files.py',
    'board/host/audit_video_pixels.py', 'board/host/audit_business_video.py',
    'board/host/reconcile_business_session.py',
    'data/lut/original_npy/model_s_x2_4bit_int8.npy','data/lut/original_npy/model_s_x4_5bit_int8.npy')
INPUTS += ('src/ps/video_pipeline/frame_session.py', 'sim/software/ps/test_hdmi_camera_pipeline.py',
           'sim/software/ps/test_hdmi_checked_copy.py', 'sim/software/ps/test_hdmi_camera.py',
           'board/host/analyze_camera_performance.py', 'sim/software/nexys/test_camera_performance.py',
           'sim/software/ps/test_hdmi_camera_optimized.py', 'sim/software/ps/test_hdmi_scanout.py',
           'sim/software/ps/test_hdmi_acp_receive.py', 'sim/software/ps/test_hdmi_pixels.py', 'sim/software/ps/test_video_pipeline.py',
           'src/ps/hdmi_display/native/hdmi_pixels.c', 'src/ps/hdmi_display/native/libhdmi_pixels.so',
           'src/ps/hdmi_display/native/build.json')
INPUTS += ('sim/software/ps/test_hdmi_metrics.py','sim/software/host/test_business_demo.py','board/pynq/bounded_logs.py',
           'sim/software/ps/test_hdmi_compare_layout.py',
           'src/ps/hdmi_display/fonts/dejavusans.ttf','src/ps/hdmi_display/fonts/dejavusans-bold.ttf',
           'src/ps/hdmi_display/fonts/license.txt')
INPUTS = tuple(name.replace('board/host/test_', 'sim/software/nexys/test_') for name in INPUTS
               if not name.startswith('board/host/package_n5_'))
INPUTS += ('sim/software/fixtures/nexys_restored.rpt', 'tools/archive_store.py', 'tools/package_current.py', 'board/release/inputs.json', 'board/release/package_template.json')
INPUTS = tuple(sorted(set(INPUTS) | {
    p.relative_to(ROOT).as_posix() for p in (ROOT/'src/ps/hdmi_display').glob('*.py')} | {
    p.relative_to(ROOT).as_posix() for p in (ROOT/'src/reference').glob('model_s_*.py')}))
TESTS = ('test_device_status', 'test_ftdi_capture', 'test_ftdi_status', 'test_business_session', 'test_business_session_loader', 'test_business_session_supervisor',
         'test_hdmi_session_trace.TraceTests', 'test_hdmi_session_trace.ObserverTests', 'test_hdmi_engine', 'test_hdmi_runtime',
         'test_business_package_binding', 'test_business_evidence_files',
         'test_bootpy_once', 'test_business_recovery_status', 'test_business_query',
         'test_business_final_media.FinalMediaTests', 'test_business_operator', 'test_hdmi_workspace', 'test_hdmi_display',
         'test_hdmi_camera_pipeline', 'test_hdmi_acp_receive',
         'test_hdmi_camera_optimized.DirectCameraTests', 'test_hdmi_scanout',
         'test_hdmi_checked_copy', 'test_hdmi_camera.CameraTests.test_default_format_preserves_negotiated_full_resolution_and_rate',
         'test_camera_performance', 'test_hdmi_presentation',
         'test_video_pipeline.VideoPipelineTests.test_external_display_allocations_are_not_freed_by_sr_session',
         'test_video_pipeline.VideoPipelineTests.test_overlap_executes_after_dma_launch_before_wait_and_recovers_on_error')

TESTS += ('test_hdmi_metrics','test_business_demo','test_hdmi_compare_layout')

def read(p):
    return json.loads(p.read_text('utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def inventory(folder):
    for item in read(folder/'manifest.json')['files']:
        path = (folder/item['path']).resolve()
        path.relative_to(folder.resolve())
        require(not path.is_symlink() and sha(path) == item['sha256'], 'Changed evidence: '+item['path'])


def check_artifacts(candidate=None):
    if candidate is not None:
        require(candidate.resolve() == HARDWARE.resolve(), 'Unexpected N5 candidate directory')
    require(sha(HARDWARE/'manifest.json') == HARDWARE_SHA, 'Changed N5 offline qualification')
    inventory(HARDWARE)
    hardware = read(HARDWARE/'result.json')
    require(hardware['candidate_id'] == '26101201' and hardware['offline_tests_passed'] is True and
            hardware['complete_vdma_identity_simulation'] is True and hardware['bitstream_generated'] is True,
            'N5 candidate lacks completed qualification')
    source = read(ROOT/SOURCE)
    require(sha(ROOT/source['archive']) == source['package_sha256'], 'Changed business ZIP')
    payload = resolve_payload(source, ROOT)
    require(sha(payload/'hdmi_business_package.json') == source['package_manifest_sha256'],
            'Changed business manifest')
    packaged = read(payload/'hdmi_business_package.json')
    files = {item['path']: item['sha256'] for item in packaged['files']}
    require(len(files) == len(packaged['files']) == source['files'], 'Invalid source inventory')
    require(packaged['source_commit'] == source['source_commit'], 'Wrong packaged source commit')
    folder = payload.resolve()
    for name, expected in files.items():
        path = (folder/name).resolve(strict=True)
        path.relative_to(folder)
        require(sha(path) == expected, 'Changed packaged file: '+name)
    require(set(source['changed_sources']) == {n for n in files if n.endswith('.py')},
            'Source contract must bind all packaged Python modules')
    for name in source['changed_sources']:
        require(sha(ROOT/name) == files[name], 'Qualified source differs from package: '+name)
    return source


def check(package, candidate=None):
    check_artifacts(candidate)
    manifest = read(package/'manifest.json')
    require(manifest['contract'] == CONTRACT and manifest['hardware_manifest_sha256'] == HARDWARE_SHA,
            'Wrong session contract')
    host = (ROOT/manifest['host']).resolve()
    host.relative_to(ROOT/'report/validation')
    require(sha(host/'manifest.json') == manifest['host_sha256'], 'Changed session qualification')
    inventory(host)
    result = read(host/'result.json')
    require(result['passed'] is True and result['hardware_access'] is False and result['tests'] >= 25 and
            result['skipped'] == 0 and result['powershell_parse_passed'] is True and result['contract'] == CONTRACT,
            'Host session checks incomplete')
    require({item['path'] for item in result['inputs']} == set(INPUTS), 'Unbound session dependency')
    for item in result['inputs']:
        require(sha(ROOT/item['path']) == item['sha256'], 'Changed session source: '+item['path'])
        data = subprocess.check_output(['git', 'show', manifest['source_commit']+':'+item['path']], cwd=ROOT)
        require(hashlib.sha256(data).hexdigest() == item['sha256'], 'Uncommitted session bytes: '+item['path'])
    data = subprocess.check_output(['git', 'show', manifest['source_commit']+':'+host.relative_to(ROOT).as_posix()+'/manifest.json'], cwd=ROOT)
    require(data == (host/'manifest.json').read_bytes(), 'Uncommitted host evidence')
    return dict(passed=True, session_loading_eligible=True, **CONTRACT)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('check', 'seal'))
    p.add_argument('package', type=Path)
    p.add_argument('--host', type=Path)
    p.add_argument('--candidate', type=Path)
    a = p.parse_args()
    if a.mode == 'seal':
        host, out = a.host.resolve(), a.package.resolve()
        host.relative_to(ROOT/'report/validation')
        out.relative_to(ROOT/'build/artifacts')
        out.mkdir(parents=True, exist_ok=False)
        m = dict(contract=CONTRACT, hardware_manifest_sha256=HARDWARE_SHA,
                 source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                 host=host.relative_to(ROOT).as_posix(), host_sha256=sha(host/'manifest.json'))
        (out/'manifest.json').write_bytes((json.dumps(m, indent=2)+'\n').encode())
    print(json.dumps(check(a.package, a.candidate)))
