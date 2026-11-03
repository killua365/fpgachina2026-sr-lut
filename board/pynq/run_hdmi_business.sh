#!/bin/sh
set -eu
business_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
business_python=/home/xilinx/sr_lut_hdmi_envs/hdmi_m1_20260909_r11/bin/python3
test -x "$business_python" || { echo 'Isolated HDMI environment missing' >&2; exit 2; }
export XILINX_XRT=/usr
exec "$business_python" -B "$business_root/board/pynq/hdmi_business_app.py" "$@"
