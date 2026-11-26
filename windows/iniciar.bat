@echo off
rem MacroPad 514c:8850 - abre la UI nueva (backend local + ventana app)
where py >nul 2>nul
if %errorlevel%==0 (
  py "%~dp0lanzar.py"
) else (
  python "%~dp0lanzar.py"
)
