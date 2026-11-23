@echo off
rem 研发物料管家 RMLM 本地一键启动（Windows）
rem
rem   start.bat            一键启动（首次会自动建 Python 虚拟环境、安装依赖、构建前端）
rem   start.bat rebuild    重新构建前端后启动（改了 frontend\ 代码后用）
rem
rem 配置写在仓库根目录的 .env（首次运行会从 .env.example 复制一份）。
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%CD%"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=start"
if /i not "%MODE%"=="start" if /i not "%MODE%"=="rebuild" (
  echo 未知参数：%MODE%（可用：start / rebuild）
  goto :fail
)

rem ---------------------------------------------------------------- 配置
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo ==^> 已生成 .env（默认连接本机 MySQL 的 rmlm 库），如需修改数据库地址请编辑 %ROOT%\.env
)
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
  if not "%%a"=="" set "%%a=%%b"
)
if not defined RMLM_HOST set "RMLM_HOST=0.0.0.0"
if not defined RMLM_PORT set "RMLM_PORT=8000"
if not defined RMLM_PHOTO_DIR set "RMLM_PHOTO_DIR=%ROOT%\backend\data\photos"
if not defined RMLM_STATIC_DIR set "RMLM_STATIC_DIR=%ROOT%\frontend\dist"

rem ---------------------------------------------------------------- Python 环境
where python >nul 2>nul
if errorlevel 1 (
  echo 错误：未找到 python，请先安装 Python 3.11 及以上版本，并勾选 "Add python.exe to PATH"
  goto :fail
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
  echo 错误：Python 版本过低，需要 3.11 及以上
  goto :fail
)
set "VENV=%ROOT%\backend\.venv"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" (
  echo ==^> 创建 Python 虚拟环境 backend\.venv
  python -m venv "%VENV%"
  if errorlevel 1 goto :fail
)
fc /b "backend\requirements.txt" "%VENV%\.requirements.txt" >nul 2>nul
if errorlevel 1 (
  echo ==^> 安装后端依赖
  "%PY%" -m pip install -q --upgrade pip
  "%PY%" -m pip install -q -r "backend\requirements.txt"
  if errorlevel 1 goto :fail
  copy /y "backend\requirements.txt" "%VENV%\.requirements.txt" >nul
)

rem ---------------------------------------------------------------- 前端
set "NEED_BUILD=0"
if /i "%MODE%"=="rebuild" set "NEED_BUILD=1"
if not exist "%RMLM_STATIC_DIR%\index.html" set "NEED_BUILD=1"
if "%NEED_BUILD%"=="1" (
  call :build_frontend || goto :fail
)

rem ---------------------------------------------------------------- 数据库连通性
cd /d "%ROOT%\backend"
"%PY%" -c "from sqlalchemy import create_engine, text; from app.config import settings; c = create_engine(settings.database_url).connect(); c.execute(text('SELECT 1')); c.close()"
if errorlevel 1 (
  echo.
  echo 错误：连不上数据库。请确认 MySQL 已启动、库已创建，并检查 .env 里的 RMLM_DATABASE_URL
  goto :fail
)

rem ---------------------------------------------------------------- 启动
echo ==^> 启动完成后浏览器打开 http://localhost:%RMLM_PORT%  （Ctrl+C 停止）
"%PY%" -m uvicorn app.main:app --host %RMLM_HOST% --port %RMLM_PORT%
goto :eof

:build_frontend
where npm >nul 2>nul
if errorlevel 1 (
  echo 错误：未找到 npm，请先安装 Node.js 18 及以上版本
  exit /b 1
)
pushd "%ROOT%\frontend"
fc /b "package-lock.json" "node_modules\.package-lock.copy" >nul 2>nul
if errorlevel 1 (
  echo ==^> 安装前端依赖
  call npm ci --no-audit --no-fund
  if errorlevel 1 ( popd & exit /b 1 )
  copy /y "package-lock.json" "node_modules\.package-lock.copy" >nul
)
echo ==^> 构建前端
call npm run build
if errorlevel 1 ( popd & exit /b 1 )
popd
exit /b 0

:fail
echo.
pause
exit /b 1
