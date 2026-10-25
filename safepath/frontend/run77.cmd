@echo off
cd /d c:\Github\HackIO\hack-io\safepath\frontend
call node_modules\.bin\vitest.cmd run src/components src/App.test.tsx src/a11y.test.tsx > tmp_t77_vitest.txt 2>&1
call node_modules\.bin\tsc.cmd --noEmit > tmp_t77_tsc.txt 2>&1
echo done > tmp_t77_done.txt
