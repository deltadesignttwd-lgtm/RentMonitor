#!/bin/bash
# Runs the OpenRent SE13 5HU scraper. Meant to be triggered by cron.
# Assumes this script sits in the same folder as scrape_openrent.py, requirements.txt, and .env.

cd "$(dirname "$0")"
python3 scrape_openrent.py >> scrape.log 2>&1
