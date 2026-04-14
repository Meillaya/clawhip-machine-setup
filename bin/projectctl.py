#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "clawhip"
PROJECTS_PATH = CONFIG_DIR / "projects.json"
DEFAULT_KEYWORDS = "error,failed,panic,traceback,merged"
ROOT_STATE = ".clawhip/state/prompt-submit.json"

LANE_LABELS = {
    "architect": "ARCH",
    "executor": "EXEC",
    "reviewer": "REVIEW",
}

KEEPALIVE_INTERVAL_MIN = {
    "architect": 40,
    "executor": 30,
    "reviewer": 45,
}

PROMPT_FILES = {
    "architect": ".clawhip/architect-keepalive-prompt.txt",
    "executor": ".clawhip/keepalive-prompt.txt",
    "reviewer": ".clawhip/reviewer-keepalive-prompt.txt",
}

SEED_PROMPT_FILES = {
    "architect": ".clawhip/architect-seed-prompt.txt",
    "reviewer": ".clawhip/reviewer-seed-prompt.txt",
}

DEFAULT_KEEPALIVE_PROMPTS = {
    "architect": "Work the architecture/planning lane for this repository. Re-read repo guidance, local plans, recent progress, and open blockers. Clarify the next safe plan, decomposition, tradeoff, or verification strategy needed to keep the executor and reviewer lanes moving.",
    "executor": "Continue the current highest-priority safe task for this repository as part of the Discord-directed autonomous workflow. Re-read repo guidance, check local plans/state, review open questions, continue implementation or verification work already in flight, and move the current lane to the next concrete checkpoint. If progress stalls, inspect tests, logs, GitHub state, and repo notes before pausing.",
    "reviewer": "Review the latest repo-local progress in this repository. Check diffs, tests, logs, and repo notes, identify the highest-value review feedback or verification gap, and continue that review lane until the next concrete checkpoint.",
}

DEFAULT_SEED_PROMPTS = {
    "architect": "You are the stable architect/planner lane for this repository. Focus on requirements clarity, task decomposition, architectural tradeoffs, sequencing, and unblock plans for the active work. Start by reading repo guidance and the latest repo-local planning/state artifacts.",
    "reviewer": "You are the stable reviewer lane for this repository. Focus on verification, code review, risk detection, test evidence, and unresolved blockers in the current work. Start by reading repo guidance and inspecting the latest in-flight changes before giving feedback.",
}


def run(cmd: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None, timeout: int | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update({k: v for k, v in env.items() if v is not None})
    return subprocess.run(cmd, cwd=cwd, env=merged_env, text=True, capture_output=True, timeout=timeout, check=check)


def print_run(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout + proc.stderr).strip()


def load_projects() -> dict[str, Any]:
    if not PROJECTS_PATH.exists():
        return {"projects": []}
    return json.loads(PROJECTS_PATH.read_text())


def save_projects(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def get_project(key: str) -> dict[str, Any]:
    data = load_projects()
    for project in data.get("projects", []):
        if project.get("key") == key:
            return project
    raise SystemExit(f"unknown project key: {key}")


def lane_session(project: dict[str, Any], lane: str) -> str:
    sessions = project.get("sessions") or {}
    return sessions.get(lane) or f"{project['key']}-{lane}"


def ensure_repo_prompts(project: dict[str, Any]) -> None:
    root = Path(project["root"])
    clawhip_dir = root / ".clawhip"
    clawhip_dir.mkdir(parents=True, exist_ok=True)
    project_json = clawhip_dir / "project.json"
    if not project_json.exists():
        project_json.write_text(json.dumps({
            "id": project["key"],
            "name": project.get("name") or project["key"],
            "repo_name": project["key"],
            "repo_url": project.get("github_repo", ""),
        }, indent=2) + "\n")
    for lane, rel in PROMPT_FILES.items():
        path = root / rel
        if not path.exists():
            path.write_text(DEFAULT_KEEPALIVE_PROMPTS[lane] + "\n")
    for lane, rel in SEED_PROMPT_FILES.items():
        path = root / rel
        if not path.exists() and lane in DEFAULT_SEED_PROMPTS:
            path.write_text(DEFAULT_SEED_PROMPTS[lane] + "\n")


def register_project(args: argparse.Namespace) -> int:
    data = load_projects()
    projects = data.setdefault("projects", [])
    project = next((p for p in projects if p.get("key") == args.key), None)
    if project is None:
        project = {"key": args.key}
        projects.append(project)
    project.update({
        "name": args.name or project.get("name") or args.key,
        "root": str(Path(args.root).resolve()),
        "github_repo": args.github_repo or project.get("github_repo", ""),
        "command_channel_id": args.command_channel_id or project.get("command_channel_id", ""),
        "sessions": {
            "architect": f"{args.key}-architect",
            "executor": f"{args.key}-codex",
            "reviewer": f"{args.key}-reviewer",
        },
    })
    save_projects(data)
    ensure_repo_prompts(project)
    print(f"registered project {args.key} -> {project['root']}")
    return 0


def lane_up(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    ensure_repo_prompts(project)
    root = project["root"]
    lane = args.lane
    session = lane_session(project, lane)
    try:
        run(["tmux", "has-session", "-t", session], check=True)
        created = False
    except subprocess.CalledProcessError:
        run(["tmux", "new-session", "-d", "-s", session, "-c", root, "codex --dangerously-bypass-approvals-and-sandbox"], check=True)
        created = True
    run(["systemctl", "--user", "start", f"clawhip-tmux-watch@{session}.service"], check=True)
    if created and lane in SEED_PROMPT_FILES:
        seed = Path(root) / SEED_PROMPT_FILES[lane]
        if seed.exists():
            subprocess.run(["sleep", "2"])
            run(["tmux", "send-keys", "-t", f"{session}:0.0", "-l", seed.read_text().strip()], check=True)
            run(["tmux", "send-keys", "-t", f"{session}:0.0", "Enter"], check=True)
    print(f"lane {lane} ready for {args.project} ({session})")
    return 0


def lanes_up(args: argparse.Namespace) -> int:
    for lane in ["architect", "executor", "reviewer"]:
        lane_up(argparse.Namespace(project=args.project, lane=lane))
    for unit in [
        f"clawhip-project-architect-keepalive@{args.project}.timer",
        f"clawhip-project-executor-keepalive@{args.project}.timer",
        f"clawhip-project-reviewer-keepalive@{args.project}.timer",
        f"clawhip-project-followup@{args.project}.timer",
        f"clawhip-project-heartbeat@{args.project}.timer",
    ]:
        subprocess.run(["systemctl", "--user", "start", unit], check=False)
    print(f"all lanes up for {args.project}")
    return 0


def keepalive(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    ensure_repo_prompts(project)
    root = Path(project["root"])
    lane = args.lane
    session = lane_session(project, lane)
    prompt_file = Path(args.prompt_file) if args.prompt_file else root / PROMPT_FILES[lane]
    prompt = prompt_file.read_text().strip() if prompt_file.exists() else DEFAULT_KEEPALIVE_PROMPTS[lane]

    try:
        run(["/home/mei/.cargo/bin/clawhip", "status"], cwd=str(root), check=True)
    except subprocess.CalledProcessError:
        print("clawhip daemon is not healthy", file=sys.stderr)
        return 1

    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write(prompt + "\n")
            temp_path = fh.name
        result = run(["/bin/bash", str(root / "src/scripts/clawhip-keepalive.sh")], cwd=str(root), env={
            "CLAWHIP_TARGET_SESSION": session,
            "CLAWHIP_KEEPALIVE_PROMPT_FILE": temp_path,
            "CLAWHIP_DELIVER_TIMEOUT_SEC": str(args.timeout),
        }, timeout=args.timeout + 30, check=False)
        os.unlink(temp_path)
        sys.stdout.write(print_run(result) + "\n")
        return 0 if result.returncode == 0 else result.returncode
    except Exception:
        pane = run(["tmux", "list-panes", "-t", session, "-F", "#{pane_id}|#{pane_active}"], check=True)
        active = next((line.split("|")[0] for line in pane.stdout.splitlines() if line.endswith("|1")), None)
        if not active:
            print("no active pane found", file=sys.stderr)
            return 1
        run(["tmux", "send-keys", "-t", active, "-l", prompt], check=True)
        run(["tmux", "send-keys", "-t", active, "Enter"], check=True)
        print(f"fallback injected prompt into {active}")
        return 0


def heartbeat(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    lanes = [args.lane] if args.lane else ["architect", "executor", "reviewer"]
    for lane in lanes:
        session = lane_session(project, lane)
        try:
            run(["tmux", "has-session", "-t", session], check=True)
            tmux_state = "up"
        except subprocess.CalledProcessError:
            tmux_state = "down"
        watch_unit = f"clawhip-tmux-watch@{session}.service"
        watch_state = run(["systemctl", "--user", "show", watch_unit, "-p", "ActiveState", "--value"], check=False)
        summary = f"tmux={tmux_state} watch={watch_state.stdout.strip() or 'unknown'} repo={project['key']}"
        run(["/home/mei/.cargo/bin/clawhip", "emit", "lane.heartbeat", "--", "--lane", lane, "--session", session, "--repo_name", project["key"], "--project", project["key"], "--summary", summary], cwd=project["root"], check=True)
        print(f"heartbeat sent for {project['key']}:{lane}")
    return 0


def followup(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    repo_slug = project.get("github_repo") or ""
    summary = f"Architecture follow-up for {repo_slug or project['key']}."
    if repo_slug:
        try:
            issues = run(["gh", "issue", "list", "-R", repo_slug, "--state", "open", "--limit", "3", "--json", "number,title", "--jq", ".[] | \"#\\(.number) \\(.title)\""], check=False)
            prs = run(["gh", "pr", "list", "-R", repo_slug, "--state", "open", "--limit", "3", "--json", "number,title", "--jq", ".[] | \"#\\(.number) \\(.title)\""], check=False)
            summary += f" Issues: {issues.stdout.strip() or 'none'}. PRs: {prs.stdout.strip() or 'none'}."
        except Exception:
            pass
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        fh.write(summary + " Review current GitHub state and decide the next planning move.\n")
        path = fh.name
    code = keepalive(argparse.Namespace(project=args.project, lane="architect", prompt_file=path, timeout=5))
    os.unlink(path)
    return code


def handoff(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    summary = " ".join(args.summary).strip()
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        fh.write(f"Handoff from {args.from_lane} to {args.to_lane}. Summary: {summary}. Take over this lane now and continue until the next checkpoint.\n")
        path = fh.name
    code = keepalive(argparse.Namespace(project=args.project, lane=args.to_lane, prompt_file=path, timeout=5))
    os.unlink(path)
    run(["/home/mei/.cargo/bin/clawhip", "emit", "lane.handoff", "--", "--from_lane", args.from_lane, "--to_lane", args.to_lane, "--repo_name", project["key"], "--project", project["key"], "--summary", summary], cwd=project["root"], check=False)
    print(f"handoff {args.from_lane}->{args.to_lane} sent for {args.project}")
    return code


def dashboard(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    print(json.dumps(project, indent=2))
    print("\n== tmux sessions ==")
    print(run(["tmux", "ls"], check=False).stdout)
    print("== watch units ==")
    for lane in ["architect", "executor", "reviewer"]:
        session = lane_session(project, lane)
        print(f"{lane}: " + run(["systemctl", "--user", "show", f"clawhip-tmux-watch@{session}.service", "-p", "ActiveState", "-p", "SubState", "--value"], check=False).stdout.replace("\n", "|").strip("|"))
    return 0



def status(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    if not args.lane:
        return dashboard(argparse.Namespace(project=args.project))
    lane = args.lane
    session = lane_session(project, lane)
    try:
        run(["tmux", "has-session", "-t", session], check=True)
        tmux_state = "up"
    except subprocess.CalledProcessError:
        tmux_state = "down"
    watch = run(["systemctl", "--user", "show", f"clawhip-tmux-watch@{session}.service", "-p", "ActiveState", "-p", "SubState"], check=False)
    timer_unit = {
        "architect": f"clawhip-project-architect-keepalive@{args.project}.timer",
        "executor": f"clawhip-project-executor-keepalive@{args.project}.timer",
        "reviewer": f"clawhip-project-reviewer-keepalive@{args.project}.timer",
    }[lane]
    timer = run(["systemctl", "--user", "status", timer_unit, "--no-pager", "--full"], check=False)
    pane = run(["tmux", "capture-pane", "-pt", f"{session}:0.0"], check=False)
    print(f"{LANE_LABELS[lane]} lane ({session})")
    print(f"tmux: {tmux_state}")
    print("watch:", watch.stdout.replace("\n", ", ").strip(", "))
    trigger = next((line.strip() for line in timer.stdout.splitlines() if "Trigger:" in line), "Trigger: unknown")
    print(trigger)
    print("pane:")
    print("\n".join(pane.stdout.strip().splitlines()[-8:]))
    return 0


def list_projects(args: argparse.Namespace) -> int:
    data = load_projects()
    for project in data.get("projects", []):
        print(f"{project.get('key')}	{project.get('root')}	{project.get('command_channel_id','')}")
    return 0

def set_channel(args: argparse.Namespace) -> int:
    data = load_projects()
    for project in data.get("projects", []):
        if project.get("key") == args.project:
            project["command_channel_id"] = args.channel_id
            save_projects(data)
            print(f"updated channel for {args.project} -> {args.channel_id}")
            return 0
    raise SystemExit(f"unknown project key: {args.project}")

def set_default(args: argparse.Namespace) -> int:
    env_path = CONFIG_DIR / "discord-command-bot.env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    kept = [line for line in lines if not line.startswith("DISCORD_DEFAULT_PROJECT=")]
    kept.append(f"DISCORD_DEFAULT_PROJECT={args.project}")
    env_path.write_text("\n".join(kept).strip() + "\n")
    print(f"set DISCORD_DEFAULT_PROJECT={args.project} in {env_path}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register")
    p.add_argument("key")
    p.add_argument("root")
    p.add_argument("--name")
    p.add_argument("--github-repo")
    p.add_argument("--command-channel-id")
    p.set_defaults(func=register_project)

    p = sub.add_parser("lane-up")
    p.add_argument("project")
    p.add_argument("lane", choices=["architect", "executor", "reviewer"])
    p.set_defaults(func=lane_up)

    p = sub.add_parser("lanes-up")
    p.add_argument("project")
    p.set_defaults(func=lanes_up)

    p = sub.add_parser("keepalive")
    p.add_argument("project")
    p.add_argument("lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("--prompt-file")
    p.add_argument("--timeout", type=int, default=5)
    p.set_defaults(func=keepalive)

    p = sub.add_parser("heartbeat")
    p.add_argument("project")
    p.add_argument("lane", nargs="?", choices=["architect", "executor", "reviewer"])
    p.set_defaults(func=heartbeat)

    p = sub.add_parser("followup")
    p.add_argument("project")
    p.set_defaults(func=followup)

    p = sub.add_parser("handoff")
    p.add_argument("project")
    p.add_argument("from_lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("to_lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("summary", nargs=argparse.REMAINDER)
    p.set_defaults(func=handoff)

    p = sub.add_parser("dashboard")
    p.add_argument("project")
    p.set_defaults(func=dashboard)

    p = sub.add_parser("list")
    p.set_defaults(func=list_projects)

    p = sub.add_parser("set-channel")
    p.add_argument("project")
    p.add_argument("channel_id")
    p.set_defaults(func=set_channel)

    p = sub.add_parser("set-default")
    p.add_argument("project")
    p.set_defaults(func=set_default)

    p = sub.add_parser("status")
    p.add_argument("project")
    p.add_argument("lane", nargs="?", choices=["architect", "executor", "reviewer"])
    p.set_defaults(func=status)

    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
