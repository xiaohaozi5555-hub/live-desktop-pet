@echo off
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" services\bus\broker.py
