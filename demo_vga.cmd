@echo off
setlocal
call "%~dp0tools\python.cmd" host "%~dp0build\candidates\nexys_vga_26102101\test_candidate.py" --demo %*
exit /b %errorlevel%
