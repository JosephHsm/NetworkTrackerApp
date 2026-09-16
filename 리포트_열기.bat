@echo off
rem Open reports via a local server so the OSM detailed map loads (file:// is blocked by OSM).
cd /d "%~dp0"
python analysis\serve_reports.py %*
if errorlevel 1 pause
