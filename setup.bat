@echo off
setlocal
title setup tool nhac v2.1

echo ======================================================
echo    🎵 TIKTOK AUDIO AUTOMATION - SETUP WIZARD 🎵
echo ======================================================
echo.

:: 1. Kiểm tra Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Khong tim thay Python! Vui long cai dat Python 3.10 tro len.
    pause
    exit /b
)

:: 2. Tạo môi trường ảo (Virtual Environment)
echo [1/4] Dang tao moi truong ao (.venv)...
python -m venv .venv
if %errorlevel% neq 0 (
    echo [ERROR] Khong the tao .venv.
    pause
    exit /b
)

:: 3. Kích hoạt và nâng cấp pip
echo [2/4] Dang nang cap pip...
call .venv\Scripts\activate
python -m pip install --upgrade pip

:: 4. Cài đặt dependencies (Tối ưu theo phần cứng)
echo [3/4] Dang kiem tra phan cung (GPU vs CPU)...

nvidia-smi >nul 2>&1
if %errorlevel% equ 0 (
    echo [INFO] Da phat hien Card do hoa NVIDIA. Dang cai dat PyTorch phien ban GPU (Tối ưu hiệu năng)...
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
) else (
    echo [INFO] Khong tim thay Card NVIDIA. Dang cai dat PyTorch phien ban CPU (Tiet kiem dung luong)...
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
)

echo Dang cai dat cac thu vien con lai...
if exist "tool_nhac\requirements.txt" (
    pip install -r tool_nhac\requirements.txt
)
if exist "tool_sroll_feed\requirements.txt" (
    pip install -r tool_sroll_feed\requirements.txt
)

:: Cai them torchcodec de ho tro VAD
pip install torchcodec



:: 5. Cài đặt Playwright Browser
echo [4/4] Dang cai dat Playwright Browsers...
playwright install chromium
playwright install-deps

echo.
echo ======================================================
echo ✅ CHUC MUNG! SETUP HOAN TAT.
echo ======================================================
echo.
echo Huong dan su dung:
echo 1. Mo Terminal/CMD tai thu muc nay.
echo 2. Go: call .venv\Scripts\activate
echo 3. Go: cd tool_nhac
echo 4. Go: python server.py
echo.
echo => Sau do mo trinh duyet truy cap: http://localhost:8000
echo.
pause
