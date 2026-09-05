@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul
  if not exist ".venv\Scripts\python.exe" (
    "%LocalAppData%\Programs\Python\Python313\python.exe" -m venv .venv
  )
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3.12 or later is required to create .venv.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
python -c "import streamlit,requests,pandas,pydantic,yaml,rapidfuzz,segno" >nul 2>&1
if errorlevel 1 (
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
  )
)

echo.
python -c "from phone_access import print_launch_help; print_launch_help()"
echo.
netsh advfirewall firewall show rule name="DraftLock Lite" >nul 2>&1
if errorlevel 1 (
  netsh advfirewall firewall add rule name="DraftLock Lite" dir=in action=allow protocol=TCP localport=8501 enable=yes profile=any >nul 2>&1
  if errorlevel 1 (
    echo If the phone cannot connect, allow Python through Windows Firewall for private networks.
  )
)

python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless false
