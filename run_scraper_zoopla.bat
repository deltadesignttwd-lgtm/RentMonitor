@echo off
REM Runs the Zoopla SE13 5HU scraper. Double-click this, or hook it up to
REM its own Windows Task Scheduler task, same as run_scraper.bat.
REM Assumes this .bat file sits in the same folder as scrape_zoopla.py, requirements.txt, and .env.

cd /d "%~dp0"
python scrape_zoopla.py >> scrape_zoopla.log 2>&1
