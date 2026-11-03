"""Apply the VGA-only experiment to an isolated reproduction snapshot."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
WORK = ROOT/'build/reproduction_work/nexys_vga_26102101'
ID = '26102101'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Unexpected baseline: '+before)
    return text.replace(before, after)


def main():
    relative = Path('src/rtl/nexys_bridge/aux_channel.vhd')
    original = ROOT/relative
    target = WORK/'source'/relative
    if target.read_bytes() != original.read_bytes():
        raise ValueError('Need an unmodified isolated source snapshot')
    text = target.read_text()
    text = replace_once(text,
        'hpd_present & power_link_live & "00" & power_flags(3 downto 0) &',
        'hpd_present & power_link_live & "0" & power_flags(4 downto 0) &')
    text = replace_once(text,
        'if payload_buffer(0)/=x"01" and payload_buffer(0)/=x"02" then next_state<=error; end if;',
        '''-- VGA candidate only: a successful 0x00 pre-read must not
                        -- prevent an ACK-checked D0 write and settling delay.
                        if payload_buffer(0)/=x"00" and payload_buffer(0)/=x"01"
                           and payload_buffer(0)/=x"02" then next_state<=error; end if;''')
    text = replace_once(text,
        '''if payload_buffer(0)=x"01" then power_flags(3)<='1';
                        else next_state<=error;end if;''',
        '''if payload_buffer(0)=x"01" then power_flags(3)<='1';
                        elsif payload_buffer(0)=x"00" and power_before=x"00"
                              and power_flags(1)='1' then
                            -- Candidate quirk: an adapter may ACK D0 while its
                            -- power register remains zero. Do not claim D0
                            -- readback; expose bit4 and require actual CR/EQ,
                            -- symbol/alignment lock and normal link monitoring.
                            power_flags(4)<='1';
                        else next_state<=error;end if;''')
    text = replace_once(text,
        '-- Experimental opt-in. Flags: bit0 pre-read complete, bit1 D0 write ACK,',
        '-- VGA candidate: bit4 zero-register quirk used; bit0 pre-read complete, bit1 D0 write ACK,')
    target.write_text(text, encoding='utf-8', newline='\n')
    shutil.copy2(target, HERE/'aux_channel.vhd')
    route = WORK/'nexys_route.tcl'
    text = route.read_text()
    text = replace_once(text,
        'if {$candidate_id ne "26101201"} {error "Continuous clock profile requires 26101201"}',
        'if {$candidate_id ne "26102101"} {error "VGA-only candidate requires 26102101"}')
    text = text.replace('launch_runs n3_vdma_synth_1 -jobs 4', 'launch_runs n3_vdma_synth_1 -jobs 1')
    text = 'set_param general.maxThreads 2\n' + text
    text += '''
# Candidate generation only. No hardware commands and no Flash operations.
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {[llength $setup]!=1 || [llength $hold]!=1} {error "Missing candidate timing paths"}
if {[get_property SLACK $setup]<0 || [get_property SLACK $hold]<0} {error "Candidate timing failed"}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == "Critical Warning"}]]} {error "Candidate DRC failed"}
set_property BITSTREAM.CONFIG.USR_ACCESS 0x26102101 [current_design]
write_bitstream [file join $out pattern.bit]
set f [open [file join $out candidate_bit_created.txt] w]
puts $f "candidate=26102101\\nnormal_demo_eligible=false\\nflash_write_allowed=false"
close $f
puts "VGA_CANDIDATE_BIT_CREATED_NO_HARDWARE"
'''
    route.write_text(text, encoding='utf-8', newline='\n')
    metadata = dict(schema='nexys_vga_candidate_v1', candidate_id=ID,
        base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        base_bit_sha256=sha(ROOT/'build/artifacts/nexys/bit/pattern.bit'),
        default_artifacts_unchanged=True, default_source_unchanged=True,
        flash_write_allowed=False, normal_demo_eligible=False,
        source_patch=dict(path=relative.as_posix(), base_sha256=sha(original), candidate_sha256=sha(target)),
        route_script_sha256=sha(route), work=WORK.relative_to(ROOT).as_posix(),
        policy='Allow zero pre-read; require D0 write ACK; allow zero post-read only after zero pre-read + ACK; record quirk bit4, never D0 bit3; keep all physical training checks.',
        reference='https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/display/drm_dp_helper.c',
        reference_scope='drm_dp_link_power_up reads the register, writes D0, waits >=1 ms without requiring a pre-existing D0/D3 value. Zero post-read fallback is this isolated experiment, not a standards compliance claim.')
    (HERE/'source.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
