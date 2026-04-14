# Fresh machine bootstrap

## Minimal bootstrap

```bash
git clone https://github.com/Meillaya/clawhip-machine-setup.git
cd clawhip-machine-setup
./scripts/install.sh
```

## Full bootstrap with first project registration

```bash
./scripts/bootstrap-machine.sh \
  --enable-linger \
  --project-key myproj \
  --project-root /absolute/path/to/repo \
  --project-name "My Project" \
  --github-repo owner/repo \
  --channel-id 123456789012345678 \
  --set-default
```

This will:
- install the machine-wide control plane under `~/.config/clawhip`
- install user systemd templates
- optionally enable user lingering
- register the first project
- start that project's architect/executor/reviewer lanes
- optionally set the default project for the Discord bot


## Register project from Discord

The machine-wide Discord bot now supports a `/register-project` slash command for onboarding an existing local repository directly from Discord. It can also map the current channel to that project immediately.


## Clone + register

```bash
python3 ~/.config/clawhip/bin/projectctl.py clone-register https://github.com/owner/repo.git /absolute/path/to/clone --key myproj --lanes-up
```
