# Operations

## Core machine-wide paths

- `~/.config/clawhip/projects.json`
- `~/.config/clawhip/bin/projectctl.py`
- `~/.config/clawhip/bin/discord-command-bot.ts`
- `~/.config/clawhip/discord-command-bot.env`

## Generic project timers

- `clawhip-project-architect-keepalive@<project>.timer`
- `clawhip-project-executor-keepalive@<project>.timer`
- `clawhip-project-reviewer-keepalive@<project>.timer`
- `clawhip-project-followup@<project>.timer`
- `clawhip-project-heartbeat@<project>.timer`

## Generic commands

```bash
python3 ~/.config/clawhip/bin/projectctl.py list
python3 ~/.config/clawhip/bin/projectctl.py register <key> /path/to/repo --github-repo owner/repo
python3 ~/.config/clawhip/bin/projectctl.py set-channel <key> <discord_channel_id>
python3 ~/.config/clawhip/bin/projectctl.py set-default <key>
python3 ~/.config/clawhip/bin/projectctl.py lanes-up <key>
python3 ~/.config/clawhip/bin/projectctl.py dashboard <key>
```


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
