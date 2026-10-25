@echo off
node node_modules\vitest\vitest.mjs run src/components/ios src/components/shell src/maps > t64_vt.txt 2>&1
echo EXIT=%ERRORLEVEL% >> t64_vt.txt
echo DONE >> t64_vt.txt
