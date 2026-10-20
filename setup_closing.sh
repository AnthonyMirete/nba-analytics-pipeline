#!/bin/bash
# setup_closing.sh: saves closing lines automatically (checks every 10 minutes).
# Run it once from Terminal:   bash setup_closing.sh
# To turn it off later:        bash setup_closing.sh off

LABEL="com.nbamodel.closing"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
FOLDER="$HOME/nba-model"

if [ "$1" = "off" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "Closing-line collection turned off."
  exit 0
fi

PYTHON="$(command -v python3)"
if [ -z "$PYTHON" ]; then
  echo "Could not find python3. Is Python installed?"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$FOLDER/closing_lines.py</string>
  </array>
  <key>WorkingDirectory</key><string>$FOLDER</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$FOLDER/closing_log.txt</string>
  <key>StandardErrorPath</key><string>$FOLDER/closing_log.txt</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"

if launchctl list | grep -q "$LABEL"; then
  echo "All set. Every 10 minutes your Mac saves closing lines for games about to tip off."
  echo "Each check is logged in closing_log.txt."
else
  echo "Something went wrong. Copy this whole message and send it to Claude."
fi
