# Operator handbook

This is the day-to-day human workflow for the machine-wide `clawhip` orchestration setup.

## Mental model

- `clawhip` is the machine-wide control plane
- each registered project gets architect / executor / reviewer lanes
- Discord is the human control surface
- `projectctl.py` is the local authority for project registration and lane lifecycle

## Daily commands

### In Discord
- `HELP`
- `PROJECTS`
- `STATUS`
- `LANES UP`
- `ARCH FOLLOWUP`
- `HANDOFF ARCH -> EXEC: ...`
- `ARCH: ...` / `EXEC: ...` / `REVIEW: ...`
- `/status`, `/lanes-up`, `/handoff`, `/prompt`, `/map-channel`

### Locally
```bash
python3 ~/.config/clawhip/bin/projectctl.py list
python3 ~/.config/clawhip/bin/projectctl.py dashboard <project>
python3 ~/.config/clawhip/bin/projectctl.py lanes-up <project>
python3 ~/.config/clawhip/bin/projectctl.py status <project> architect
python3 ~/.config/clawhip/bin/projectctl.py followup <project>
python3 ~/.config/clawhip/bin/projectctl.py handoff <project> architect executor "summary"
```

## Adding a new project

### Existing local repo
```bash
python3 ~/.config/clawhip/bin/projectctl.py register <key> /absolute/path/to/repo \
  --name "Project Name" \
  --github-repo owner/repo \
  --command-channel-id 123456789012345678
```

### Clone and register in one step
```bash
python3 ~/.config/clawhip/bin/projectctl.py clone-register https://github.com/owner/repo.git /absolute/path/to/clone \
  --key myproj \
  --name "My Project" \
  --command-channel-id 123456789012345678 \
  --lanes-up
```

## Channel mapping

- text/slash commands in a mapped Discord channel target that project automatically
- you can also map a channel from Discord with `/map-channel`
- or locally with:

```bash
python3 ~/.config/clawhip/bin/projectctl.py set-channel <project> <channel_id>
```

## Security notes

- restrict bot command channels tightly
- prefer `DISCORD_ALLOWED_USERS` when possible
- rotate bot tokens if they were ever exposed in logs or chat


## Register project from Discord

The machine-wide Discord bot now supports a `/register-project` slash command for onboarding an existing local repository directly from Discord. It can also map the current channel to that project immediately.
