@echo off
setlocal
call "%~dp0tools\python.cmd" host "%~dp0board\host\demo_session.py" %*
exit /b %errorlevel%
