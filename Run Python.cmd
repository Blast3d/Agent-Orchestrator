@echo off
setlocal
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" %*
) else (
  if exist "%~dp0PACKAGE-MANIFEST.json" (
    echo Bundled Python is missing. Extract the complete Windows ZIP again.
    exit /b 1
  )
  python %*
)
exit /b %errorlevel%
