"""Independent SR-LUT integer-reference and lossy-codec measurements.

One RGB frame at a time. The model and LUT are the existing numerical contract;
compression PSNR compares decoded MP4 against pre-encode RGB, never against an
invented HR reference. No quality threshold or board acceptance is inferred.
"""
import hashlib
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src/reference'))
from model_s_output import run_model_s
from model_s_interpolator import load_model_s_lut, get_lut_spec
from audit_hdmi_business_pixels import digest


def measure(raw_input, raw_output, movie, clip, scale, *, ffmpeg='ffmpeg', scratch=None):
    w, h, count = clip['width'], clip['height'], clip['frame_count']
    frame_bytes = w*h*scale*scale*3
    expected_bytes = frame_bytes*count
    if raw_output.stat().st_size != expected_bytes:
        raise ValueError('Retained output RGB length mismatch')
    bits = {2:4, 4:5}[scale]
    spec = get_lut_spec(scale, bits)
    lut_path = ROOT/'data/lut/original_npy'/spec['filename']
    lut = load_model_s_lut(lut_path, scale, bits)
    rate = clip['fps_numerator']/clip['fps_denominator']
    scratch = Path(scratch) if scratch is not None else ROOT/'build/n5_video_audit_tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(scratch).free < expected_bytes+128*1024*1024:
        raise OSError('Insufficient local scratch space for independent MP4 decoding')
    before = [digest(p) for p in (raw_input,raw_output,movie)]
    with tempfile.TemporaryDirectory(prefix='decode_',dir=scratch) as directory:
        decoded = Path(directory)/'decoded.rgb'
        # Disk staging bounds RAM and subprocess timeout bounds a blocked codec.
        with tempfile.TemporaryFile() as errors:
            result = subprocess.run([ffmpeg,'-nostdin','-v','error','-xerror','-i',str(movie),
                '-map','0:v:0','-an','-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo','-n',str(decoded)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors,
                timeout=max(60,count/rate*20))
            errors.seek(0)
            diagnostic=errors.read(8192).decode('utf-8','replace')
        if result.returncode or diagnostic.strip():
            raise ValueError('MP4 RGB decoding failed: '+diagnostic)
        if decoded.stat().st_size != expected_bytes:
            raise ValueError('Decoded RGB length differs from the declared complete timeline')
        differing_frames=0; differing_pixels=0; first_mismatch=None
        squared_error=0; max_error=0; worst_frame_mse=0.0
        golden_hash=hashlib.sha256(); output_hash=hashlib.sha256()
        with raw_input.open('rb') as source, raw_output.open('rb') as output, decoded.open('rb') as encoded:
            for index in range(count):
                src=np.frombuffer(source.read(w*h*3),np.uint8).reshape(h,w,3)
                raw=output.read(frame_bytes)
                actual=np.frombuffer(raw,np.uint8).reshape(h*scale,w*scale,3)
                lossy=np.frombuffer(encoded.read(frame_bytes),np.uint8).reshape(actual.shape)
                golden=run_model_s(src,lut,scale,bits)
                golden_hash.update(golden.tobytes()); output_hash.update(raw)
                mismatch=int(np.count_nonzero(np.any(actual!=golden,axis=2)))
                if mismatch:
                    differing_frames+=1; differing_pixels+=mismatch
                    if first_mismatch is None: first_mismatch=index
                delta=actual.astype(np.int16)-lossy.astype(np.int16)
                error=int(np.sum(delta.astype(np.int64)**2))
                squared_error+=error
                max_error=max(max_error,int(np.max(np.abs(delta))))
                worst_frame_mse=max(worst_frame_mse,error/frame_bytes)
        mse=squared_error/(count*frame_bytes)
    if before != [digest(p) for p in (raw_input,raw_output,movie)]:
        raise ValueError('Video evidence changed during pixel analysis')
    return dict(frames=count, sr_golden_exact=differing_frames==0,
        differing_frames=differing_frames,differing_pixels=differing_pixels,first_mismatch=first_mismatch,
        reference=dict(scale=scale,sampling_bits=bits,lut_sha256=spec['sha256'].lower(),
            model_sources={p.name:digest(p) for p in sorted((ROOT/'src/reference').glob('model_s_*.py'))}),
        preencode_sha256=output_hash.hexdigest(),golden_sha256=golden_hash.hexdigest(),
        compression=dict(reference='actual pre-encode RGB, not ground-truth HR',
            rgb_mse=mse,psnr_rgb_db=10*math.log10(255**2/mse) if mse else None,
            rgb_identical=mse==0,max_absolute_channel_error=max_error,
            worst_frame_mse=worst_frame_mse,frames=count,quality_threshold=None,quality_passed=None),
        displayed_on_board_verified=False,n5_passed=False)
