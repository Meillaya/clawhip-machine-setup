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
