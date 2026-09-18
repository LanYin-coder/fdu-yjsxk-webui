@echo off
chcp 65001 >nul
title FDU Course Helper
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_windows.ps1"
if errorlevel 1 pause
