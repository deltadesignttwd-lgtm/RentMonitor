@echo off
REM Runs all three rent scrapers (OpenRent, Zoopla, Rightmove) one after
REM another. Meant to be triggered manually (double-click) or by Windows
REM Task Scheduler instead of three separate scheduled tasks.
REM Assumes this .bat file sits in the same folder as scrape_openrent.py,
REM scrape_zoopla.py, scrape_rightmove.py, requirements.txt, and .env.

cd /d "%~dp0"

echo ==== OpenRent ==== >> scrape_all.log
python scrape_openrent.py >> scrape_all.log 2>&1

echo. >> scrape_all.log
echo ==== Zoopla ==== >> scrape_all.log
python scrape_zoopla.py >> scrape_all.log 2>&1

echo. >> scrape_all.log
echo ==== Rightmove ==== >> scrape_all.log
python scrape_rightmove.py >> scrape_all.log 2>&1
