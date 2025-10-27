@echo off
setlocal enabledelayedexpansion

:: Đặt đường dẫn đầy đủ
set "ROOT=%~dp0"
:: Remove trailing backslash if exists
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "DIST_DIR=%ROOT%\dist"
set "HISTORICAL_DIR=%DIST_DIR%\historical"
set "GEN_TOKEN_DIR=%DIST_DIR%\gen_token"
set "NODEAPP_DIR=%ROOT%\nodeapp"

:: Xóa toàn bộ nội dung trong thư mục dist nếu tồn tại
if exist "%DIST_DIR%" (
    echo Đang xóa toàn bộ nội dung trong thư mục dist...
    rmdir /S /Q "%DIST_DIR%"
    if exist "%DIST_DIR%" (
        echo Không thể xóa thư mục dist. Vui lòng đóng các chương trình đang sử dụng.
        pause
        exit /b 1
    )
)

:: Tạo lại thư mục dist
mkdir "%DIST_DIR%"

:: 1. Copy file từ nodeapp sang gen_token
echo.
echo [1/4] Đang copy file từ nodeapp sang gen_token...
if exist "%NODEAPP_DIR%" (
    echo Đang copy từ: %NODEAPP_DIR%
    echo Đang copy đến: %GEN_TOKEN_DIR%
    
    if not exist "%GEN_TOKEN_DIR%" mkdir "%GEN_TOKEN_DIR%"
    
    robocopy "%NODEAPP_DIR%" "%GEN_TOKEN_DIR%" /E /COPY:DAT /R:3 /W:1 /NP /NFL /NDL
    if %ERRORLEVEL% GEQ 8 (
        echo Lỗi khi copy file từ nodeapp. Mã lỗi: %ERRORLEVEL%
        pause
        exit /b 1
    )
    echo Đã copy xong file từ nodeapp
) else (
    echo Lỗi: Không tìm thấy thư mục nodeapp tại: %NODEAPP_DIR%
    pause
    exit /b 1
)

:: 2. Copy file cấu hình
echo.
echo [2/4] Đang copy file cấu hình...

:: Tạo thư mục historical nếu chưa tồn tại
if not exist "%HISTORICAL_DIR%" mkdir "%HISTORICAL_DIR%"

:: Copy file .env từ thư mục gốc vào dist/historical
if exist "%ROOT%\.env" (
    copy /Y "%ROOT%\.env" "%HISTORICAL_DIR%\" >nul
    echo Đã copy .env chính vào %HISTORICAL_DIR%
) else if exist "%~dp0..\.env" (
    copy /Y "%~dp0..\.env" "%HISTORICAL_DIR%\" >nul
    echo Đã copy .env chính từ thư mục cha vào %HISTORICAL_DIR%
) else (
    echo Không tìm thấy file .env trong thư mục gốc hoặc thư mục cha
)

:: Copy file historical_config.json (bắt buộc)
set "CONFIG_FOUND=0"
if exist "%ROOT%\historical_config.json" (
    copy /Y "%ROOT%\historical_config.json" "%HISTORICAL_DIR%\" >nul
    set "CONFIG_FOUND=1"
    echo Đã copy historical_config.json
) else if exist "%~dp0..\historical_config.json" (
    copy /Y "%~dp0..\historical_config.json" "%HISTORICAL_DIR%\" >nul
    set "CONFIG_FOUND=1"
    echo Đã copy historical_config.json từ thư mục cha
) else (
    echo Không tìm thấy file historical_config.json trong thư mục gốc hoặc thư mục cha
)

if "%CONFIG_FOUND%"=="0" (
    echo Lỗi: Không tìm thấy historical_config.json trong thư mục gốc hoặc thư mục cha.
    pause
    exit /b 1
)

:: 3. Copy launcher files TRƯỚC KHI BUILD
echo.
echo [3/5] Đang copy launcher files...

:: Copy PowerShell script để disable Quick Edit
if exist "%ROOT%\disable_quickedit.ps1" (
    copy /Y "%ROOT%\disable_quickedit.ps1" "%HISTORICAL_DIR%\" >nul
    echo Đã copy disable_quickedit.ps1
) else (
    echo CẢNH BÁO: Không tìm thấy disable_quickedit.ps1
)

:: Copy launcher bat file
if exist "%ROOT%\run_without_quickedit.bat" (
    copy /Y "%ROOT%\run_without_quickedit.bat" "%HISTORICAL_DIR%\" >nul
    echo Đã copy run_without_quickedit.bat
) else (
    echo CẢNH BÁO: Không tìm thấy run_without_quickedit.bat
)

:: 4. Build file thực thi
echo.
echo [4/5] Đang build file thực thi...
cd /d "%ROOT%"
pkg . --targets node18-win-x64 --output "%HISTORICAL_DIR%\historical.exe"
if %ERRORLEVEL% NEQ 0 (
    echo Lỗi khi build file thực thi. Mã lỗi: %ERRORLEVEL%
    pause
    exit /b 1
)

:: 5. Verify files
echo.
echo [5/5] Đang kiểm tra files...

:: Hiển thị thông báo hoàn tất
echo.
echo ========================================
echo ĐÃ HOÀN TẤT BUILD!
echo ========================================
echo.
echo File thực thi: %HISTORICAL_DIR%\historical.exe
echo Thư mục gen_token: %GEN_TOKEN_DIR%
echo.
echo ========================================
echo FILES ĐÃ TẠO:
echo ========================================
echo   ✅ historical.exe                - Main application
echo   ✅ run_without_quickedit.bat     - Launcher với Quick Edit DISABLED
echo   ✅ disable_quickedit.ps1         - PowerShell fix script
echo   ✅ gen_token/                    - Chứa websocket_tokens.csv
echo.
echo ========================================
echo HƯỚNG DẪN CHẠY (ANTI-FREEZE):
echo ========================================
echo.
echo cd /d "%HISTORICAL_DIR%"
echo run_without_quickedit.bat
echo.
echo ⚠️  LƯU Ý QUAN TRỌNG:
echo    - LUÔN dùng run_without_quickedit.bat để khởi chạy
echo    - Console sẽ tự động disable Quick Edit Mode
echo    - KHÔNG click chuột vào console khi chạy
echo    - Nhấn Ctrl+C để dừng an toàn
echo.
pause