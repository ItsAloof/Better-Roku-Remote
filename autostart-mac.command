#!/bin/sh
# Double-click to turn autostart ON (or OFF if it's already on).
# When on, the remote runs in the background and starts by itself whenever you log in.
cd "$(dirname "$0")" || exit 1
LABEL="com.betterrokuremote.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$PLIST" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "Autostart is now OFF and the background remote has stopped."
else
  PY="$(command -v python3)"
  if [ -z "$PY" ]; then
    echo "Python 3 isn't installed. Get it from https://www.python.org/downloads/"
  else
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$(pwd)/roku_remote.py</string></array>
  <key>WorkingDirectory</key><string>$(pwd)</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$(pwd)/remote.log</string>
  <key>StandardErrorPath</key><string>$(pwd)/remote.log</string>
</dict></plist>
PL
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
    echo "Autostart is now ON. The remote is running in the background and restarts"
    echo "by itself if it ever stops. Double-click this file again to turn it off."
    echo
    echo "If macOS asks to let Python find devices on your local network, click Allow."
  fi
fi
printf '\nPress Enter to close...'
read _
