@echo off
cd /d c:\Github\HackIO\hack-io\safepath\frontend
call node_modules\.bin\vitest.cmd run src/components/shell > vt76.tmp 2>&1
call node_modules\.bin\tsc.cmd --noEmit > tsc76.tmp 2>&1
echo done > done76.tmp
