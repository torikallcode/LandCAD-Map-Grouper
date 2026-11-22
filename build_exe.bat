@echo off
REM Build LandCAD Map Grouper with PyInstaller.
REM Output: dist\LandCADMapGrouper\LandCADMapGrouper.exe

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment tidak ditemukan. Jalankan:
    echo     py -3.11 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

echo [1/3] Memastikan PyInstaller tersedia...
.venv\Scripts\python.exe -m pip install --quiet pyinstaller
if errorlevel 1 exit /b 1

echo [2/3] Membersihkan hasil build sebelumnya...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] Membangun aplikasi...
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm LandCADMapGrouper.spec
if errorlevel 1 exit /b 1

echo.
echo Selesai. Jalankan:
echo     dist\LandCADMapGrouper\LandCADMapGrouper.exe
endlocal
