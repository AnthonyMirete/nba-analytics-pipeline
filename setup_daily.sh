#!/bin/bash
# setup_daily.sh: runs the whole routine (stats, props, injuries, model, grading, parlays) every day at 1:00pm.
# Run it once from Terminal:   bash setup_daily.sh
# To turn the daily run off later:   bash setup_daily.sh off

# What time the daily routine runs (24-hour clock). 13 = 1:00pm.
RUN_HOUR=13

LABEL="com.nbamodel.saveprops"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
FOLDER="$HOME/nba-model"

if [ "$1" = "off" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "Daily run turned off."
  exit 0
fi

PYTHON="$(command -v python3)"
if [ -z "$PYTHON" ]; then
  echo "Could not find python3. Is Python installed?"
  exit 1
fi

# The daily routine: fresh stats, today's lines, the model, then grading.
cat > "$FOLDER/daily_run.sh" <<RUNEOF
#!/bin/bash
cd "$FOLDER"
echo ""
echo "===== \$(date) ====="
"$PYTHON" get_stats.py
"$PYTHON" save_props.py
"$PYTHON" get_injuries.py
"$PYTHON" ml_model.py
"$PYTHON" simple_model.py
"$PYTHON" grade.py
"$PYTHON" parlay.py
RUNEOF

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$FOLDER/daily_run.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$FOLDER</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$RUN_HOUR</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$FOLDER/daily_log.txt</string>
  <key>StandardErrorPath</key><string>$FOLDER/daily_log.txt</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"

if launchctl list | grep -q "$LABEL"; then
  echo "All set. Every day at ${RUN_HOUR}:00 your Mac will: download stats, save props, check injuries, run the model, grade past bets and pick parlays."
  echo "Each run's output is written to daily_log.txt in your nba-model folder."
else
  echo "Something went wrong. Copy this whole message and send it to Claude."
fi
