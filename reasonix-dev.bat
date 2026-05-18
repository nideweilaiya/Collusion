@echo off
cd /d "%~dp0"
title Reasonix Dev — Collusion Edition
echo ========================================
echo   Reasonix Dev + Collusion Skill
echo   版本: 0.46.1 (自构建)
echo   斜杠命令: /collusion /共谋
echo ========================================
echo.
node D:/Reasonix-Dev/dist/cli/index.js code %*
