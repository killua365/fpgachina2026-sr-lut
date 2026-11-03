"""Hidden owned Windows jobs; workers cannot launch children before assignment.

Microsoft: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
Closing a job handle kills its tree. Forced termination NEVER implies FPGA recovery.
"""
import ctypes as C
from ctypes import wintypes as W
import json,os,subprocess
from pathlib import Path

class BASIC(C.Structure):
    _fields_=[('ProcessTime',C.c_longlong),('JobTime',C.c_longlong),('Flags',W.DWORD),
              ('MinWorkingSet',C.c_size_t),('MaxWorkingSet',C.c_size_t),('ActiveLimit',W.DWORD),
              ('Affinity',C.c_size_t),('Priority',W.DWORD),('Scheduling',W.DWORD)]
class IO(C.Structure):
    _fields_=[(n,C.c_ulonglong) for n in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
class EXTENDED(C.Structure):
    _fields_=[('Basic',BASIC),('Io',IO),('ProcessMemory',C.c_size_t),('JobMemory',C.c_size_t),
              ('PeakProcess',C.c_size_t),('PeakJob',C.c_size_t)]

def kernel():
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[C.c_void_p,W.LPCWSTR];k.CreateJobObjectW.restype=W.HANDLE
    k.SetInformationJobObject.argtypes=[W.HANDLE,C.c_int,C.c_void_p,W.DWORD];k.SetInformationJobObject.restype=W.BOOL
    k.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE];k.AssignProcessToJobObject.restype=W.BOOL
    k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
    k.QueryPerformanceCounter.argtypes=[C.POINTER(C.c_longlong)];k.QueryPerformanceCounter.restype=W.BOOL
    k.QueryPerformanceFrequency.argtypes=[C.POINTER(C.c_longlong)];k.QueryPerformanceFrequency.restype=W.BOOL
    return k

def qpc_ns():
    k=kernel();value=C.c_longlong();frequency=C.c_longlong()
    if not k.QueryPerformanceCounter(C.byref(value)) or not k.QueryPerformanceFrequency(C.byref(frequency)):
        raise C.WinError(C.get_last_error())
    return value.value*1_000_000_000//frequency.value

class OwnedProcess:
    def __init__(self,folder,executable,arguments,cwd):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=False)
        self.k=kernel();self.job=self.k.CreateJobObjectW(None,None)
        self.process=None;self.log=None
        if not self.job:raise C.WinError(C.get_last_error())
        try:
            limits=EXTENDED();limits.Basic.Flags=0x2000 # KILL_ON_JOB_CLOSE; no breakaway
            if not self.k.SetInformationJobObject(self.job,9,C.byref(limits),C.sizeof(limits)):
                raise C.WinError(C.get_last_error())
            go=self.folder/'go';spec=self.folder/'worker.json'
            spec.write_text(json.dumps(dict(go=str(go),cwd=str(cwd),executable=str(executable),arguments=list(map(str,arguments)))))
            self.log=(self.folder/'console.txt').open('wb')
            self.process=subprocess.Popen([powershell(),'-NoProfile','-NonInteractive','-File',
                str(Path(__file__).with_name('longrun_worker.ps1')),'-Spec',str(spec)],
                cwd=cwd,stdout=self.log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            if not self.k.AssignProcessToJobObject(self.job,int(self.process._handle)):
                # Worker is still waiting at the barrier and has no children.
                self.process.terminate();self.process.wait(timeout=10)
                raise C.WinError(C.get_last_error())
            go.write_text('Assigned to owned job; allowed to launch specified command.\n')
        except BaseException:
            self.close();raise
    def poll(self):return self.process.poll()
    def close(self):
        if self.job:
            if not self.k.CloseHandle(self.job):raise C.WinError(C.get_last_error())
            self.job=None
        if self.process is not None:
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:raise RuntimeError('Owned job termination not confirmed')
        if self.log:self.log.close();self.log=None

def powershell():
    return str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
