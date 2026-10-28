@echo off
rem Builds dist\Shelfkeeper.exe (one file, no console). Needs the .venv with requirements + pyinstaller.
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install -q pyinstaller || goto :error
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name Shelfkeeper ^
    --icon assets\icon.ico --add-data "assets\icon.png;assets" ^
    --exclude-module tkinter --exclude-module pytest ^
    shelfkeeper.pyw || goto :error
echo.
echo Built: "%~dp0dist\Shelfkeeper.exe"
exit /b 0
:error
echo Build failed.
exit /b 1
