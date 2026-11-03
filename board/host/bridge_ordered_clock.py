"""Same Windows QPC basis used by capture and supervisor; no hardware access."""
from longrun_process import qpc_ns

if __name__=='__main__': print(qpc_ns())
