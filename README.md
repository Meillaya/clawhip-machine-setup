# clawhip-machine-setup

Reusable machine-wide orchestration setup for `clawhip` + tmux-backed Codex lanes + Discord command control.

This repository packages the machine-wide control plane currently installed under `~/.config/clawhip` into a reproducible repo you can clone, inspect, and re-install on another machine.

## What it contains

- generic project registry: `bin/projectctl.py`
- machine-wide Discord bot/parser: `bin/discord-command-bot.ts`
- systemd user unit templates for per-project lane keepalives/followups/heartbeats
- environment template for the Discord bot
- example projects registry
- install/bootstrap script

## Quick install

```bash
cd clawhip-machine-setup
./scripts/install.sh
```

## Fresh machine bootstrap

```bash
./scripts/bootstrap-machine.sh --enable-linger --project-key myproj --project-root /absolute/path/to/repo --github-repo owner/repo --set-default
```

This installs to:

- `~/.config/clawhip/`
- `~/.config/systemd/user/`

## Register a project

```bash
python3 ~/.config/clawhip/bin/projectctl.py register myproj /absolute/path/to/repo \
  --name "My Project" \
  --github-repo owner/repo \
  --command-channel-id 123456789012345678
```

## Bring lanes up

```bash
python3 ~/.config/clawhip/bin/projectctl.py lanes-up myproj
```

## Machine-wide Discord bot

Fill:

- `~/.config/clawhip/discord-command-bot.env`

Then start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now clawhip-discord-command-bot.service
```

## Notes

- The control plane name is `clawhip`, not project-specific.
- This repo intentionally contains templates and generic control logic, not project secrets.


See `docs/bootstrap.md` for the full fresh-machine bootstrap flow.


See `docs/operator-handbook.md` for the day-to-day operator workflow and multi-project command patterns.


## Register project from Discord

The machine-wide Discord bot now supports a `/register-project` slash command for onboarding an existing local repository directly from Discord. It can also map the current channel to that project immediately.


## Clone + register

```bash
python3 ~/.config/clawhip/bin/projectctl.py clone-register https://github.com/owner/repo.git /absolute/path/to/clone --key myproj --lanes-up
```


## Clone and register from Discord

The machine-wide Discord bot now supports a `/clone-register` slash command for cloning a remote repo to a local path, registering it in the machine-wide registry, and optionally mapping the current channel.


## Formal supervisor + workflow modes

The machine-wide control plane now maintains formal supervisor state in `~/.config/clawhip/supervisor-state.json`.

Supported workflow invocations include:
- `$team` / `/team`
- `$ralph` / `/ralph`
- `SUPERVISOR STATUS` / `/supervisor-status`

These commands update project mode/phase/lane status and dispatch prompts to the appropriate lanes.
