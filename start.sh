#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "  Python 3 is not installed. Get it from python.org"; exit 1; }

if [ ! -d ".venv" ]; then
  echo "  First run. Setting things up, this takes a minute..."
  python3 -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip --quiet
  pip install -r requirements.txt --quiet
else
  . .venv/bin/activate
fi

command -v ffmpeg >/dev/null || echo "  Heads up: FFmpeg was not found, so rendering will fail. See docs/SETUP.md"

python app.py
