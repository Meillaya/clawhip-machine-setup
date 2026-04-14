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
SUPERVISOR_STATE_PATH = CONFIG_DIR / "supervisor-state.json"

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

def load_supervisor_state() -> dict[str, Any]:
    if not SUPERVISOR_STATE_PATH.exists():
        return {"projects": {}}
    return json.loads(SUPERVISOR_STATE_PATH.read_text())

def save_supervisor_state(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SUPERVISOR_STATE_PATH.write_text(json.dumps(data, indent=2) + "\n")

def now_iso() -> str:
    return subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True).stdout.strip()

def default_supervisor_project(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": project["key"],
        "project_name": project.get("name") or project["key"],
        "mode": "manual",
        "phase": "analysis",
        "owner_lane": "architect",
        "blockers": [],
        "lane_status": {
            "architect": {"state": "idle", "summary": "", "updated_at": now_iso()},
            "executor": {"state": "idle", "summary": "", "updated_at": now_iso()},
            "reviewer": {"state": "idle", "summary": "", "updated_at": now_iso()},
        },
        "history": [],
        "updated_at": now_iso(),
    }

def ensure_supervisor_project_entry(project: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    data = load_supervisor_state()
    projects = data.setdefault("projects", {})
    entry = projects.get(project["key"])
    if entry is None:
        entry = default_supervisor_project(project)
        projects[project["key"]] = entry
    return data, entry

def supervisor_event(entry: dict[str, Any], event: str, summary: str, lane: str | None = None) -> None:
    record = {"at": now_iso(), "event": event, "summary": summary}
    if lane:
        record["lane"] = lane
    history = entry.setdefault("history", [])
    history.append(record)
    if len(history) > 50:
        del history[:-50]
    entry["updated_at"] = record["at"]

def set_lane_state(entry: dict[str, Any], lane: str, state: str, summary: str) -> None:
    lane_status = entry.setdefault("lane_status", {})
    lane_status[lane] = {"state": state, "summary": summary, "updated_at": now_iso()}

def sync_supervisor_runtime(entry: dict[str, Any], project: dict[str, Any]) -> None:
    for lane in ["architect", "executor", "reviewer"]:
        session = lane_session(project, lane)
        try:
            run(["tmux", "has-session", "-t", session], check=True)
            tmux_state = "up"
        except subprocess.CalledProcessError:
            tmux_state = "down"
        watch = run(["systemctl", "--user", "show", f"clawhip-tmux-watch@{session}.service", "-p", "ActiveState", "--value"], check=False)
        watch_state = watch.stdout.strip() or "unknown"
        lane_status = entry.setdefault("lane_status", {}).setdefault(lane, {})
        lane_status["tmux"] = tmux_state
        lane_status["watch"] = watch_state
        lane_status.setdefault("updated_at", now_iso())
    entry["updated_at"] = now_iso()

def active_pane_for_session(session: str) -> str | None:
    pane = run(["tmux", "list-panes", "-t", session, "-F", "#{pane_id}|#{pane_active}"], check=False)
    return next((line.split("|")[0] for line in pane.stdout.splitlines() if line.endswith("|1")), None)


def dispatch_lane_prompt(project: dict[str, Any], lane: str, prompt: str, timeout: int = 5) -> tuple[int, str]:
    session = lane_session(project, lane)
    try:
        result = run([
            "/home/mei/.cargo/bin/clawhip",
            "deliver",
            "--session", session,
            "--prompt", prompt,
            "--max-enters", "4",
        ], cwd=project["root"], timeout=timeout, check=False)
        if result.returncode == 0:
            return 0, print_run(result)
    except Exception as error:
        result = subprocess.CompletedProcess([], 124, "", str(error))

    active = active_pane_for_session(session)
    if not active:
        return 1, f"unable to resolve active tmux pane for {session}"
    run(["tmux", "send-keys", "-t", active, "-l", prompt], check=True)
    run(["tmux", "send-keys", "-t", active, "Enter"], check=True)
    return 0, f"fallback injected prompt into {active}"


def workflow_prompts(project_key: str, workflow: str, prompt: str) -> dict[str, str]:
    if workflow == "team":
        return {
            "architect": f"$team mode active for {project_key}. Goal: {prompt}\nProduce or refresh the plan, identify blockers, and coordinate the next lane transitions.",
            "executor": f"$team mode active for {project_key}. Goal: {prompt}\nInspect current repo state and prepare to execute the coordinated plan. Continue implementation work when the plan is clear.",
            "reviewer": f"$team mode active for {project_key}. Goal: {prompt}\nPrepare the verification lane: inspect current diffs, likely risks, and evidence needed once execution advances.",
        }
    if workflow == "ralph":
        return {
            "executor": f"$ralph mode active for {project_key}. Persist until the task is complete and verified: {prompt}\nKeep going through clear next steps, recover from failures, and do not stop before a concrete blocker or completion.",
            "reviewer": f"$ralph companion mode for {project_key}. Track verification gaps and be ready to validate the final result for: {prompt}",
        }
    raise ValueError(f"unsupported workflow: {workflow}")


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
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    supervisor_entry["project_name"] = project.get("name") or project["key"]
    supervisor_entry["updated_at"] = now_iso()
    save_supervisor_state(supervisor_data)
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
    project = get_project(args.project)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    sync_supervisor_runtime(supervisor_entry, project)
    for lane in ["architect", "executor", "reviewer"]:
        if supervisor_entry["lane_status"].get(lane, {}).get("state") == "idle":
            set_lane_state(supervisor_entry, lane, "ready", f"lane {lane} available")
    supervisor_event(supervisor_entry, "lanes_up", f"all lanes up for {args.project}")
    save_supervisor_state(supervisor_data)
    print(f"all lanes up for {args.project}")
    return 0


def keepalive(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    ensure_repo_prompts(project)
    root = Path(project["root"])
    lane = args.lane
    prompt_file = Path(args.prompt_file) if args.prompt_file else root / PROMPT_FILES[lane]
    prompt = prompt_file.read_text().strip() if prompt_file.exists() else DEFAULT_KEEPALIVE_PROMPTS[lane]

    try:
        run(["/home/mei/.cargo/bin/clawhip", "status"], cwd=str(root), check=True)
    except subprocess.CalledProcessError:
        print("clawhip daemon is not healthy", file=sys.stderr)
        return 1

    code, output = dispatch_lane_prompt(project, lane, prompt, timeout=args.timeout)
    if output:
        sys.stdout.write(output + "\n")
    return code


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
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    supervisor_entry["phase"] = "planning"
    supervisor_entry["owner_lane"] = "architect"
    set_lane_state(supervisor_entry, "architect", "active", "GitHub follow-up planning")
    supervisor_event(supervisor_entry, "followup", summary, lane="architect")
    save_supervisor_state(supervisor_data)
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
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    supervisor_entry["owner_lane"] = args.to_lane
    set_lane_state(supervisor_entry, args.from_lane, "handoff_sent", summary)
    set_lane_state(supervisor_entry, args.to_lane, "active", summary)
    supervisor_event(supervisor_entry, "handoff", summary, lane=args.to_lane)
    save_supervisor_state(supervisor_data)
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




def infer_project_key(repo_url: str) -> str:
    tail = repo_url.rstrip('/').split('/')[-1]
    if tail.endswith('.git'):
        tail = tail[:-4]
    return tail.replace(' ', '-').lower()

def clone_register(args: argparse.Namespace) -> int:
    key = args.key or infer_project_key(args.repo_url)
    target_dir = Path(args.dest_dir).expanduser().resolve()
    if target_dir.exists() and any(target_dir.iterdir()):
        raise SystemExit(f"destination already exists and is not empty: {target_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", args.repo_url, str(target_dir)], check=True)
    register_project(argparse.Namespace(
        key=key,
        root=str(target_dir),
        name=args.name or key,
        github_repo=args.github_repo or args.repo_url,
        command_channel_id=args.command_channel_id or "",
    ))
    if args.lanes_up:
        lanes_up(argparse.Namespace(project=key))
    if args.set_default:
        set_default(argparse.Namespace(project=key))
    print(f"cloned and registered {key} -> {target_dir}")
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

def supervisor_status(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    sync_supervisor_runtime(supervisor_entry, project)
    save_supervisor_state(supervisor_data)
    print(json.dumps(supervisor_entry, indent=2))
    return 0

def supervisor_set_mode(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    supervisor_entry["mode"] = args.mode
    if args.phase:
        supervisor_entry["phase"] = args.phase
    if args.owner_lane:
        supervisor_entry["owner_lane"] = args.owner_lane
    summary = args.summary or f"mode set to {args.mode}"
    if args.owner_lane:
        set_lane_state(supervisor_entry, args.owner_lane, "active", summary)
    supervisor_event(supervisor_entry, "set_mode", summary, lane=args.owner_lane)
    save_supervisor_state(supervisor_data)
    print(f"set supervisor mode for {args.project} -> {args.mode}")
    return 0

def supervisor_transition(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    supervisor_entry["phase"] = args.phase
    if args.lane:
        supervisor_entry["owner_lane"] = args.lane
        set_lane_state(supervisor_entry, args.lane, args.state or "active", args.summary or f"phase {args.phase}")
    supervisor_event(supervisor_entry, "transition", args.summary or f"phase -> {args.phase}", lane=args.lane)
    save_supervisor_state(supervisor_data)
    print(f"transitioned {args.project} -> {args.phase}")
    return 0

def supervisor_block(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    blocker = {"at": now_iso(), "lane": args.lane, "summary": args.summary}
    supervisor_entry.setdefault("blockers", []).append(blocker)
    supervisor_entry["phase"] = "blocked"
    set_lane_state(supervisor_entry, args.lane, "blocked", args.summary)
    supervisor_event(supervisor_entry, "block", args.summary, lane=args.lane)
    save_supervisor_state(supervisor_data)
    print(f"blocked {args.project}:{args.lane} -> {args.summary}")
    return 0

def supervisor_resolve(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    blockers = supervisor_entry.setdefault("blockers", [])
    if args.lane:
        blockers[:] = [b for b in blockers if b.get("lane") != args.lane]
        set_lane_state(supervisor_entry, args.lane, "ready", args.summary or "blocker resolved")
    else:
        blockers.clear()
    if supervisor_entry.get("phase") == "blocked":
        supervisor_entry["phase"] = args.phase or "planning"
    supervisor_event(supervisor_entry, "resolve", args.summary or "blocker resolved", lane=args.lane)
    save_supervisor_state(supervisor_data)
    print(f"resolved blockers for {args.project}")
    return 0

def invoke_workflow(args: argparse.Namespace) -> int:
    project = get_project(args.project)
    ensure_repo_prompts(project)
    prompts = workflow_prompts(project["key"], args.workflow, args.prompt)
    supervisor_data, supervisor_entry = ensure_supervisor_project_entry(project)
    supervisor_entry["mode"] = args.workflow
    if args.workflow == "team":
        supervisor_entry["phase"] = "planning"
        supervisor_entry["owner_lane"] = "architect"
        set_lane_state(supervisor_entry, "architect", "active", "team planning active")
        set_lane_state(supervisor_entry, "executor", "standby", "await coordinated execution")
        set_lane_state(supervisor_entry, "reviewer", "standby", "await review entry point")
    elif args.workflow == "ralph":
        supervisor_entry["phase"] = "coding"
        supervisor_entry["owner_lane"] = "executor"
        set_lane_state(supervisor_entry, "architect", "standby", "ralph mode delegated to executor")
        set_lane_state(supervisor_entry, "executor", "active", "ralph persistence active")
        set_lane_state(supervisor_entry, "reviewer", "standby", "await verification checkpoint")
    dispatch_results = {}
    for lane, prompt in prompts.items():
        code, output = dispatch_lane_prompt(project, lane, prompt, timeout=args.timeout)
        dispatch_results[lane] = {"code": code, "output": output[-400:]}
    supervisor_event(supervisor_entry, "invoke_workflow", f"{args.workflow}: {args.prompt}")
    supervisor_entry["last_dispatch"] = dispatch_results
    save_supervisor_state(supervisor_data)
    print(json.dumps({"workflow": args.workflow, "project": args.project, "dispatch": dispatch_results}, indent=2))
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

    p = sub.add_parser("clone-register")
    p.add_argument("repo_url")
    p.add_argument("dest_dir")
    p.add_argument("--key")
    p.add_argument("--name")
    p.add_argument("--github-repo")
    p.add_argument("--command-channel-id")
    p.add_argument("--lanes-up", action="store_true")
    p.add_argument("--set-default", action="store_true")
    p.set_defaults(func=clone_register)

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

    p = sub.add_parser("supervisor-status")
    p.add_argument("project")
    p.set_defaults(func=supervisor_status)

    p = sub.add_parser("supervisor-set-mode")
    p.add_argument("project")
    p.add_argument("mode", choices=["manual", "team", "ralph", "autopilot"])
    p.add_argument("--phase")
    p.add_argument("--owner-lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("--summary")
    p.set_defaults(func=supervisor_set_mode)

    p = sub.add_parser("supervisor-transition")
    p.add_argument("project")
    p.add_argument("phase", choices=["analysis", "planning", "coding", "review", "verification", "blocked", "complete"])
    p.add_argument("--lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("--state")
    p.add_argument("--summary")
    p.set_defaults(func=supervisor_transition)

    p = sub.add_parser("supervisor-block")
    p.add_argument("project")
    p.add_argument("lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("summary")
    p.set_defaults(func=supervisor_block)

    p = sub.add_parser("supervisor-resolve")
    p.add_argument("project")
    p.add_argument("--lane", choices=["architect", "executor", "reviewer"])
    p.add_argument("--phase", choices=["analysis", "planning", "coding", "review", "verification", "complete"])
    p.add_argument("--summary")
    p.set_defaults(func=supervisor_resolve)

    p = sub.add_parser("invoke-workflow")
    p.add_argument("project")
    p.add_argument("workflow", choices=["team", "ralph"])
    p.add_argument("prompt")
    p.add_argument("--timeout", type=int, default=5)
    p.set_defaults(func=invoke_workflow)

    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    raise SystemExit(main())
