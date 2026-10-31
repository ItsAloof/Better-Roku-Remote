#!/bin/sh
# Run (or double-click) to turn autostart ON, or OFF if it's already on.
# Good for a Raspberry Pi: the remote starts at boot and restarts if it ever stops.
cd "$(dirname "$0")" || exit 1
UNIT="better-roku-remote.service"
DIR="$HOME/.config/systemd/user"

if [ -f "$DIR/$UNIT" ]; then
  systemctl --user disable --now "$UNIT" 2>/dev/null
  rm -f "$DIR/$UNIT"; systemctl --user daemon-reload
  echo "Autostart is now OFF and the background remote has stopped."
else
  PY="$(command -v python3)"
  if [ -z "$PY" ]; then
    echo "Python 3 isn't installed. On a Pi: sudo apt install python3"
  else
    mkdir -p "$DIR"
    cat > "$DIR/$UNIT" <<UN
[Unit]
Description=Better Roku Remote
After=network-online.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=$PY $(pwd)/roku_remote.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UN
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT"
    # Keep it running when nobody is logged in (needed for start-at-boot on a Pi)
    loginctl enable-linger "$(id -un)" 2>/dev/null || sudo loginctl enable-linger "$(id -un)"
    echo "Autostart is now ON. The remote runs in the background, starts at boot,"
    echo "and restarts by itself if it ever stops. Run this again to turn it off."
    echo "Status: systemctl --user status $UNIT"
  fi
fi
printf '\nPress Enter to close...'
read _
