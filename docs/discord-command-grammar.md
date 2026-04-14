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
