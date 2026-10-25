@echo off
cd /d c:\Github\HackIO\hack-io\safepath\frontend
del frame_vt2.tmp.txt frame_done2.tmp.txt 2>nul
npx.cmd vitest run src/components/ios/frame.test.tsx > frame_vt2.tmp.txt 2>&1
echo VTEXIT=%ERRORLEVEL% >> frame_vt2.tmp.txt
echo DONE > frame_done2.tmp.txt
