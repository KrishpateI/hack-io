@echo off
cd /d c:\Github\HackIO\hack-io\safepath\frontend
node node_modules\eslint\bin\eslint.js src/components/ios/frame.test.tsx > frame_es.tmp.txt 2>&1
echo ESEXIT=%ERRORLEVEL% >> frame_es.tmp.txt
node node_modules\vitest\vitest.mjs run src/components/ios/frame.test.tsx > frame_vt.tmp.txt 2>&1
echo VTEXIT=%ERRORLEVEL% >> frame_vt.tmp.txt
echo DONE > frame_done.tmp.txt
