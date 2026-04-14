#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.config/clawhip"
SYSTEMD_DIR="$HOME/.config/systemd/user"
ENABLE_LINGER=0
PROJECT_KEY=""
PROJECT_ROOT=""
PROJECT_NAME=""
GITHUB_REPO=""
CHANNEL_ID=""
SET_DEFAULT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable-linger) ENABLE_LINGER=1 ;;
    --project-key) PROJECT_KEY="$2"; shift ;;
    --project-root) PROJECT_ROOT="$2"; shift ;;
    --project-name) PROJECT_NAME="$2"; shift ;;
    --github-repo) GITHUB_REPO="$2"; shift ;;
    --channel-id) CHANNEL_ID="$2"; shift ;;
    --set-default) SET_DEFAULT=1 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
  shift
done

mkdir -p "$CONFIG_DIR/bin" "$SYSTEMD_DIR"
install -m 755 "$REPO_ROOT/bin/projectctl.py" "$CONFIG_DIR/bin/projectctl.py"
install -m 755 "$REPO_ROOT/bin/discord-command-bot.ts" "$CONFIG_DIR/bin/discord-command-bot.ts"
install -m 644 "$REPO_ROOT/templates/discord-command-bot.env.example" "$CONFIG_DIR/discord-command-bot.env.example"
if [[ ! -f "$CONFIG_DIR/discord-command-bot.env" ]]; then
  cp "$REPO_ROOT/templates/discord-command-bot.env.example" "$CONFIG_DIR/discord-command-bot.env"
fi
if [[ ! -f "$CONFIG_DIR/projects.json" ]]; then
  cp "$REPO_ROOT/templates/projects.json.example" "$CONFIG_DIR/projects.json"
fi
install -m 644 "$REPO_ROOT/systemd/"* "$SYSTEMD_DIR/"
systemctl --user daemon-reload

if [[ "$ENABLE_LINGER" == "1" ]]; then
  loginctl enable-linger "$(whoami)" || true
fi

if [[ -n "$PROJECT_KEY" && -n "$PROJECT_ROOT" ]]; then
  python3 "$CONFIG_DIR/bin/projectctl.py" register "$PROJECT_KEY" "$PROJECT_ROOT" \
    ${PROJECT_NAME:+--name "$PROJECT_NAME"} \
    ${GITHUB_REPO:+--github-repo "$GITHUB_REPO"} \
    ${CHANNEL_ID:+--command-channel-id "$CHANNEL_ID"}
  python3 "$CONFIG_DIR/bin/projectctl.py" lanes-up "$PROJECT_KEY"
  if [[ "$SET_DEFAULT" == "1" ]]; then
    python3 "$CONFIG_DIR/bin/projectctl.py" set-default "$PROJECT_KEY"
  fi
fi

echo "Machine-wide clawhip control plane installed to $CONFIG_DIR"
echo "User systemd units installed to $SYSTEMD_DIR"
