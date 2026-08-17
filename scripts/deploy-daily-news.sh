#!/bin/zsh
# Install/update the local, read-only Daily Briefing LaunchAgent.
set -euo pipefail
label="com.tradingagents.daily-news"
root="$HOME/Library/Application Support/TradingAgentsDaily"
source_root="${0:A:h:h}"
agent="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$root"/{app,logs} "$HOME/Library/LaunchAgents"
if [[ ! -f "$source_root/.env" ]]; then
  print "Create $source_root/.env with credentials/settings, then run this script again."
  exit 1
fi
# Keep one source of truth while ensuring secrets are never placed in app/.
install -m 600 "$source_root/.env" "$root/.env"
rsync -a --delete --exclude .git --exclude .venv --exclude results --exclude .env "$source_root/" "$root/app/"
if [[ ! -x "$root/.venv/bin/python" ]]; then
  python3 -m venv "$root/.venv"
fi
"$root/.venv/bin/pip" install --upgrade pip
"$root/.venv/bin/pip" install "$root/app[daily]"
cp "$root/app/config/$label.plist" "$agent"
plutil -lint "$agent"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$agent"
launchctl enable "gui/$(id -u)/$label"
print "Installed $label. Test: launchctl kickstart -k gui/$(id -u)/$label"
