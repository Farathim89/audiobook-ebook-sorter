@echo off
rem Builds dist\BookSorter.exe (one file, no console). Needs the .venv with requirements + pyinstaller.
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install -q pyinstaller || goto :error
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name BookSorter ^
    --icon assets\icon.ico --add-data "assets\icon.png;assets" ^
    --exclude-module tkinter --exclude-module pytest ^
    booksorter.pyw || goto :error
echo.
echo Built: "%~dp0dist\BookSorter.exe"
exit /b 0
:error
echo Build failed.
exit /b 1
