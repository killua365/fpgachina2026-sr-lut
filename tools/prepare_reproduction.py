"""Prepare relocatable Nexys IP inputs or a byte-identical PYNQ source snapshot."""
import argparse, hashlib, json, shutil
from pathlib import Path
from archive_store import component
ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'build/artifacts/nexys'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--out',type=Path,required=True)
 p.add_argument('--platform',choices=('nexys','pynq'),default='nexys')
 a=p.parse_args(); out=a.out.resolve(); out.mkdir(parents=True,exist_ok=False)
 # Expand unchanged vendor components only in this isolated build workspace.
 snapshot=out/'source'; records=[]
 for folder in ('src/rtl','data/lut/bram_mem','build/scripts','sim/nexys'):
   for src in sorted((ROOT/folder).rglob('*')):
    if not src.is_file() or set(src.parts)&{'__pycache__','.git','.Xil'}:continue
    relative=src.relative_to(ROOT); dest=snapshot/relative
    dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dest)
    digest=sha(src)
    if sha(dest)!=digest:raise ValueError('Build snapshot differs: '+str(relative))
    records.append(dict(path=relative.as_posix(),sha256=digest))
 for vendor in ('digilent_video','digilent_hdmi_rx','digilent_nexys_video_board','fpga_displayport','mjpegzero'):
  component(vendor,snapshot/'src/third_party'/vendor)
 if a.platform=='pynq':
  (out/'preparation.json').write_text(json.dumps(dict(root=str(ROOT),platform='pynq',snapshot=str(snapshot),
      files=records,hardware_access=False),indent=2)+'\n',encoding='utf-8')
  print(json.dumps(dict(prepared=str(out),source_root=str(snapshot),files=len(records),hardware_access=False)))
  return
 ip=out/'ips'; ip.mkdir()
 changes=[]
 for name in ('n3_mig','n3_vdma'):
  d=ip/name; d.mkdir()
  src=FROZEN/'dependencies'/(name+'.xci')
  data=json.loads(src.read_text())
  original=json.loads(src.read_text())
  data['ip_inst']['gen_directory']='gen'
  if name=='n3_mig':
   data['ip_inst']['parameters']['component_parameters']['XML_INPUT_FILE'][0]['value']='mig.prj'
   shutil.copy2(FROZEN/'dependencies/mig.prj',d/'mig.prj')
   assert (d/'mig.prj').read_bytes()==(ROOT/'src/rtl/nexys_framebuffer/mig_128_shared_clock.prj').read_bytes()
  target=d/(name+'.xci'); target.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
  old=original['ip_inst']['parameters']['component_parameters']; new=data['ip_inst']['parameters']['component_parameters']
  for key in old:
   if key!='XML_INPUT_FILE': assert old[key]==new[key],(name,key)
  changes.append(dict(source=src.relative_to(ROOT).as_posix(),source_sha256=sha(src),prepared_sha256=sha(target),changed_fields=['gen_directory']+(['XML_INPUT_FILE'] if name=='n3_mig' else [])))
 # Explicit root permits an ASCII-only build output without changing delivery sources.
 for source,target in [('build_n5_business_board.tcl','nexys_route.tcl'),('simulate_n5_core.tcl','nexys_sim.tcl')]:
  text=(ROOT/'build/scripts/nexys'/source).read_text()
  # Vivado sources generated Tcl using the Windows system encoding. Encode the
  # path as ASCII hex, then decode UTF-8 explicitly inside Tcl so Chinese roots
  # cannot be misread as legacy-codepage bytes.
  root_expression='[encoding convertfrom utf-8 [binary format H* {'+snapshot.as_posix().encode('utf-8').hex()+'}]]'
  text=text.replace('set root [file normalize [file join [file dirname [info script]] ../../..]]','set root '+root_expression)
  text=text.replace('build/scripts/nexys/work/n3_shared_mig_r1/project/n3_shared.srcs/sources_1/ip/n3_mig/n3_mig.xci','n3_mig/n3_mig.xci')
  text=text.replace('build/scripts/nexys/work/n3_vdma_frame_r3/project/n3_vdma_frame.srcs/sources_1/ip/n3_vdma/n3_vdma.xci','n3_vdma/n3_vdma.xci')
  if source=='build_n5_business_board.tcl':
   # The original worktree already contained generated MIG HDL. A clean build
   # must regenerate it from the retained XCI and identical electrical config.
   text=text.replace('generate_target all [get_ips n3_vdma]', '''# MIG's customization callback changes into its output directory before
# generate_target creates it, so a clean project must create that directory.
file mkdir [file join $out project n5_bridge.gen sources_1 ip n3_mig]
generate_target all [get_ips n3_mig]
set mig_xci [get_files -all */n3_mig.xci]
if {[llength $mig_xci]!=1} {error "Expected exactly one MIG XCI"}
create_ip_run $mig_xci
launch_runs n3_mig_synth_1 -jobs 1
wait_on_run n3_mig_synth_1
if {[get_property STATUS [get_runs n3_mig_synth_1]] ne "synth_design Complete!"} {
    error "MIG out-of-context synthesis did not complete"
}
generate_target all [get_ips n3_vdma]''')
  (out/target).write_text(text,encoding='utf-8')
 (out/'preparation.json').write_text(json.dumps(dict(root=str(ROOT),hardware_manifest_sha256=sha(FROZEN/'manifest.json'),changes=changes,hardware_access=False),indent=2)+'\n',encoding='utf-8')
 print(json.dumps(dict(prepared=str(out),ip_source=str(ip),hardware_access=False)))
if __name__=='__main__': main()
