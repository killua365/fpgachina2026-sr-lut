"""Optional explicit PYNQ board entry; never invoked by offline checks."""
import argparse
import json
from pathlib import Path
import time

import numpy as np

from checks.validation import sha256
from templates.control_hooks import HlsControlHooks
from templates.pynq_backend import PynqBackend
from templates.session import BatchSession, array_contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if not args.download:
        parser.error("--download is required; this command changes FPGA configuration")
    profile = json.loads(args.profile.read_text("utf-8-sig"))
    control = profile["control"]
    if control["protocol"] != "hls_ap_ctrl_hs":
        raise ValueError("Implement explicit hooks for your custom control protocol")
    source = np.load(args.input, allow_pickle=False)
    shape, dtype = array_contract(profile["contract"]["input"])
    array_contract(profile["contract"]["output"])
    if source.shape != shape or source.dtype != dtype or not np.isfinite(source).all():
        raise ValueError("Input does not satisfy contract")
    timeout = profile.get("timeout_s", 2.0)
    if not np.isfinite(timeout) or timeout <= 0:
        raise ValueError("Invalid timeout")
    if control["ip"] not in profile["required_ips"]:
        raise ValueError("Control IP must be included in required_ips")
    hooks = HlsControlHooks(control["ip"], control.get("scalar_writes", []))
    backend = PynqBackend.load(profile, args.profile.resolve().parent, hooks,
                               allow_download=True)
    session = BatchSession(backend, profile["contract"], timeout_s=timeout)
    try:
        with session:
            result, measurement = session.run(source)
    except BaseException:
        if session.quarantined:
            print("DMA stop unconfirmed. Keeping buffers/process alive; retrying halt. "
                  "Restore the hardware state before exiting.", flush=True)
            while not session.closed:
                try:
                    time.sleep(1)
                    session.close()
                except (RuntimeError, KeyboardInterrupt):
                    # A Ctrl+C is not evidence that an AXI master stopped.
                    continue
        raise
    args.out.mkdir(parents=True, exist_ok=True)
    np.save(args.out/"actual.npy", result, allow_pickle=False)
    record = {"hardware_access": True, "golden_validated": False,
              "profile_sha256": sha256(args.profile), "input_sha256": sha256(args.input),
              "output_sha256": sha256(args.out/"actual.npy"), "measurement": measurement}
    (args.out/"hardware_run.json").write_text(json.dumps(record, indent=2)+"\n", "utf-8")


if __name__ == "__main__":
    main()
