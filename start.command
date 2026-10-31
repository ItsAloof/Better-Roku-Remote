#!/bin/sh
# Double-click to start the remote server (macOS). Also works as ./start.command on Linux.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 roku_remote.py
else
  echo "Python 3 isn't installed. Get it from https://www.python.org/downloads/"
fi
printf '\nPress Enter to close...'
read _
