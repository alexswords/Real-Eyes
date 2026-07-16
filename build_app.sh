#!/bin/bash
# Assembles "Real Eyes.app" from this source tree. Run on macOS: bash build_app.sh
set -e
cd "$(dirname "$0")"
APP="Real Eyes.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/Real-Eyes/templates" "$APP/Contents/Resources/Real-Eyes/static"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Real Eyes</string>
  <key>CFBundleDisplayName</key><string>Real Eyes</string>
  <key>CFBundleIdentifier</key><string>local.realeyes</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>RealEyes</string>
  <key>CFBundleIconFile</key><string>app</string>
</dict>
</plist>
EOF

cat > "$APP/Contents/MacOS/RealEyes" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources/Real-Eyes" && pwd)"
SUP="$HOME/Library/Application Support/Real-Eyes"
LOG="$SUP/server.log"
URL="http://127.0.0.1:5001"
mkdir -p "$SUP"

# migrate caches from the old MediaGrabber support folder (pre-rename installs)
OLD="$HOME/Library/Application Support/MediaGrabber"
if [ -d "$OLD" ] && [ ! -e "$SUP/.venv" ]; then
  cp -R "$OLD/." "$SUP/" 2>/dev/null || true
fi

WANT=$(cat "$DIR/VERSION" 2>/dev/null || echo dev)
if curl -s -o /dev/null --max-time 1 "$URL"; then
  HAVE=$(curl -s --max-time 1 "$URL/api/version" 2>/dev/null || true)
  if [ "$HAVE" = "$WANT" ]; then
    open "$URL"
    exit 0
  fi
  curl -s -X POST --max-time 2 "$URL/api/shutdown" >/dev/null 2>&1
  sleep 1
  if curl -s -o /dev/null --max-time 1 "$URL"; then
    PIDS=$(lsof -ti tcp:5001 2>/dev/null)
    [ -n "$PIDS" ] && kill $PIDS 2>/dev/null
    sleep 1
  fi
fi

fail() {
  osascript -e "display alert \"Real Eyes: $1\" message \"Details are in server.log (opening that folder now).\"" >/dev/null 2>&1
  open "$SUP"
  exit 1
}

{
  echo "=== launch $(date) ==="
  if [ ! -x "$SUP/.venv/bin/python" ]; then
    echo "creating venv..."
    /usr/bin/python3 -m venv "$SUP/.venv"
  fi
  if ! "$SUP/.venv/bin/python" -c "import flask, bs4, requests" 2>/dev/null; then
    echo "installing dependencies..."
    "$SUP/.venv/bin/pip" install -r "$DIR/requirements.txt"
  fi
} >> "$LOG" 2>&1

"$SUP/.venv/bin/python" -c "import flask, bs4, requests" 2>/dev/null || fail "setup failed"

nohup "$SUP/.venv/bin/python" "$DIR/app.py" >> "$LOG" 2>&1 &
disown

for i in $(seq 1 60); do
  sleep 1
  if curl -s -o /dev/null --max-time 1 "$URL"; then
    open "$URL"
    exit 0
  fi
done
fail "server did not start"
EOF
chmod +x "$APP/Contents/MacOS/RealEyes"

date +%s > VERSION
cp app.py scraper.py requirements.txt VERSION "$APP/Contents/Resources/Real-Eyes/"
cp templates/index.html "$APP/Contents/Resources/Real-Eyes/templates/"
cp static/*.svg static/*.png "$APP/Contents/Resources/Real-Eyes/static/" 2>/dev/null || cp static/*.svg "$APP/Contents/Resources/Real-Eyes/static/"
cp assets/app.icns "$APP/Contents/Resources/app.icns"
echo "Built: $APP"
