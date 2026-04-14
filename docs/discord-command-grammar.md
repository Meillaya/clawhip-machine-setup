# Discord command grammar

Supported text commands:

- `HELP`
- `PROJECTS`
- `STATUS [ARCH|EXEC|REVIEW]`
- `LANES UP`
- `ARCH UP` / `EXEC UP` / `REVIEW UP`
- `HEARTBEAT [ARCH|EXEC|REVIEW]`
- `ARCH FOLLOWUP`
- `HANDOFF ARCH -> EXEC: summary`
- `ARCH: prompt` / `EXEC: prompt` / `REVIEW: prompt`
- `MAP-CHANNEL <project-key>`

Multi-project explicit prefix:

- `PROJECT <key> STATUS`
- `PROJECT <key> LANES UP`
- `PROJECT <key> HANDOFF ARCH -> EXEC: summary`

Slash commands are also registered by the bot for mapped guilds.


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
