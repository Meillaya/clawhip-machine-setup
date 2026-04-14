#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.config/clawhip"
SYSTEMD_DIR="$HOME/.config/systemd/user"

mkdir -p "$CONFIG_DIR/bin" "$SYSTEMD_DIR"

install -m 755 "$REPO_ROOT/bin/projectctl.py" "$CONFIG_DIR/bin/projectctl.py"
install -m 755 "$REPO_ROOT/bin/discord-command-bot.ts" "$CONFIG_DIR/bin/discord-command-bot.ts"
install -m 644 "$REPO_ROOT/templates/discord-command-bot.env.example" "$CONFIG_DIR/discord-command-bot.env.example"
if [ ! -f "$CONFIG_DIR/discord-command-bot.env" ]; then
  cp "$REPO_ROOT/templates/discord-command-bot.env.example" "$CONFIG_DIR/discord-command-bot.env"
fi
if [ ! -f "$CONFIG_DIR/projects.json" ]; then
  cp "$REPO_ROOT/templates/projects.json.example" "$CONFIG_DIR/projects.json"
fi
install -m 644 "$REPO_ROOT/systemd/"* "$SYSTEMD_DIR/"
systemctl --user daemon-reload
printf 'Installed machine-wide clawhip control plane to %s\n' "$CONFIG_DIR"
printf 'Installed user systemd unit templates to %s\n' "$SYSTEMD_DIR"
