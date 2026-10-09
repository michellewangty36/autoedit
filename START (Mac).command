#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python is not installed yet. A web page will open: download Python, install it, then open this file again."
  open https://www.python.org/downloads/
  read -p "Press Enter to close"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "First start: setting up (this takes a few minutes, only once)..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python prefetch.py
fi
.venv/bin/python update.py
echo "Starting AutoEdit... your browser will open. Keep this window open while you use it."
.venv/bin/python app.py
