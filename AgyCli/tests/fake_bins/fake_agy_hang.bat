@echo off
rem Fake stand-in for agy.exe that never returns - used to prove
rem agy_client._kill_process_tree actually recovers a hung call. Batch files run as
rem cmd.exe's own child (ping, here), which is exactly the case _kill_process_tree's
rem taskkill /T /F exists for: a plain proc.kill() on the immediate cmd.exe would
rem orphan this ping process, which still holds a duplicated handle to our
rem stdout/stderr pipes, hanging a plain proc.wait() forever even though the direct
rem child is already dead.
ping -n 120 127.0.0.1 >nul
