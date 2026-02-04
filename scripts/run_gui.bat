@echo off
setlocal
set "PROJECT_DIR=%~dp0.."
set "PYTHON_EXE="

if exist "%USERPROFILE%\anaconda3\envs\gigaam\python.exe" set "PYTHON_EXE=%USERPROFILE%\anaconda3\envs\gigaam\python.exe"
if exist "%USERPROFILE%\miniconda3\envs\gigaam\python.exe" set "PYTHON_EXE=%USERPROFILE%\miniconda3\envs\gigaam\python.exe"
if exist "%LOCALAPPDATA%\Programs\Anaconda3\envs\gigaam\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Anaconda3\envs\gigaam\python.exe"
if exist "%LOCALAPPDATA%\miniconda3\envs\gigaam\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\miniconda3\envs\gigaam\python.exe"
if exist "%ProgramData%\anaconda3\envs\gigaam\python.exe" set "PYTHON_EXE=%ProgramData%\anaconda3\envs\gigaam\python.exe"

if not "%PYTHON_EXE%"=="" goto run_python

where conda >nul 2>&1
if %errorlevel% equ 0 goto use_conda_run
goto try_activate

:use_conda_run
cd /d "%PROJECT_DIR%"
conda run -n gigaam python app.py
if %errorlevel% neq 0 pause
exit /b 0

:run_python
cd /d "%PROJECT_DIR%"
"%PYTHON_EXE%" app.py
if %errorlevel% neq 0 pause
exit /b 0

:try_activate
set "CONDA_ACTIVATE="
if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\anaconda3\Scripts\activate.bat"
if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%USERPROFILE%\miniconda3\Scripts\activate.bat"
if exist "%LOCALAPPDATA%\Programs\Anaconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%LOCALAPPDATA%\Programs\Anaconda3\Scripts\activate.bat"
if exist "%LOCALAPPDATA%\miniconda3\Scripts\activate.bat" set "CONDA_ACTIVATE=%LOCALAPPDATA%\miniconda3\Scripts\activate.bat"
if "%CONDA_ACTIVATE%"=="" goto error_conda
call "%CONDA_ACTIVATE%" gigaam
if %errorlevel% neq 0 goto error_env
cd /d "%PROJECT_DIR%"
python app.py
if %errorlevel% neq 0 pause
exit /b 0

:error_conda
echo [Error] Conda not found. Install Anaconda or Miniconda.
echo Run: conda create -n gigaam python=3.10 -y
pause
exit /b 1

:error_env
echo [Error] Conda env "gigaam" not found.
echo Run: conda create -n gigaam python=3.10 -y
pause
exit /b 1
