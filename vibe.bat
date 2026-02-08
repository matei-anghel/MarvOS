@ECHO off
:: vibe wrapper - makes vibe work with Node.js v25+
:: Uses tsx to transpile TypeScript on the fly

SETLOCAL EnableDelayedExpansion

:: Get the directory where this script is located
SET "SCRIPT_DIR=%~dp0"

:: Find vibe-cli installation
IF EXIST "C:\Users\matei\AppData\Roaming\npm\node_modules\vibe-cli\index.ts" (
    SET "VIBE_PATH=C:\Users\matei\AppData\Roaming\npm\node_modules\vibe-cli\index.ts"
) ELSE (
    echo Error: vibe-cli not found
    echo Please install: npm install -g vibe
    exit /b 1
)

:: Check if tsx is available
where tsx >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo Error: tsx is not installed
    echo Installing tsx...
    npm install -g tsx
)

:: Run vibe with tsx
tsx "!VIBE_PATH!" %*
