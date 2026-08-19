@echo off
REM Runs the OpenRent SE13 5HU scraper. Meant to be triggered by Windows Task Scheduler.
REM Assumes this .bat file sits in the same folder as scrape_openrent.py, requirements.txt, and .env.

cd /d "%~dp0"
python scrape_openrent.py >> scrape.log 2>&1
