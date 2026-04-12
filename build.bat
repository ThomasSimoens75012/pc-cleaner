@echo off
echo.
echo  === OpenCleaner Build ===
echo.

:: Verifier Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERREUR] Python non trouve. Installez Python 3.10+ et ajoutez-le au PATH.
    pause
    exit /b 1
)

:: Verifier PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Installation de PyInstaller...
    pip install pyinstaller
    if errorlevel 1 (
        echo  [ERREUR] Impossible d'installer PyInstaller.
        pause
        exit /b 1
    )
)

:: Verifier les dependances
pip install -r requirements.txt >nul 2>&1

:: Build
echo  [BUILD] Compilation en cours...
pyinstaller OpenCleaner.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo  [ERREUR] La compilation a echoue.
    pause
    exit /b 1
)

echo.
echo  === Build termine ! ===
echo  Fichier : dist\OpenCleaner.exe
echo.
pause
