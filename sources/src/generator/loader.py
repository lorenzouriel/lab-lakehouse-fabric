"""``generator load``: run the generated load scripts against the compose databases, or your own servers.

Default: inside the docker compose containers, which already have psql / sqlcmd / mongoimport and mount ``./data``
at ``/lab`` (so nothing needs installing on this machine). ``--host``: run the same scripts with the clients on
this machine against whatever URL the repo-root ``.env`` says.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR, ROOT

ENV_FILE = ROOT / ".env"


@dataclass(frozen=True)
class Target:
    fmt: str            # folder under data/
    service: str        # docker compose service
    script: str         # generated script to run
    path_extra: str = ""   # client binaries that are not on PATH inside the container


TARGETS = {
    "postgresql": Target("postgresql", "postgres", "load.sh"),
    "sqlserver": Target("sqlserver", "sqlserver", "load.sh", "/opt/mssql-tools18/bin"),
    "mongodb": Target("mongodb", "mongo", "import.sh"),
}


def parse_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Plain KEY=value lines (same rules as the shell snippet in the generated scripts)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def container_env(target: str, env: dict[str, str], dataset: str) -> dict[str, str]:
    """Settings for a script running INSIDE the container: local host, container-internal port, .env credentials."""
    if target == "postgresql":
        return {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432",
                "POSTGRES_USER": env.get("POSTGRES_USER", "postgres"),
                "POSTGRES_PASSWORD": env.get("POSTGRES_PASSWORD", "labsources"),
                "POSTGRES_DATABASE": env.get("POSTGRES_DATABASE", "labsources")}
    if target == "sqlserver":
        return {"SQLSERVER_HOST": "localhost", "SQLSERVER_PORT": "1433", "SQLSERVER_SERVER": "",
                "SQLSERVER_USER": "sa",
                "SQLSERVER_PASSWORD": env.get("SQLSERVER_PASSWORD", "LabSources_2026!"),
                "SQLSERVER_DATABASE": env.get("SQLSERVER_DATABASE", "labsources"),
                "SQLSERVER_DATA_DIR": f"/lab/csv/{dataset}"}
    user, password = env.get("MONGO_USER", "labsources"), env.get("MONGO_PASSWORD", "labsources")
    uri = f"mongodb://{user}:{password}@localhost:27017/?authSource=admin" if user else "mongodb://localhost:27017"
    return {"MONGO_URI": uri, "MONGO_DATABASE": env.get("MONGO_DATABASE", "labsources")}


def docker_commands(target: str, env: dict[str, str], dataset: str) -> tuple[list[str], list[str], dict[str, str]]:
    """(start command, exec command, environment for the exec command) for one target."""
    t = TARGETS[target]
    settings = container_env(target, env, dataset)
    # -e NAME (no value) forwards the variable from our own environment, so passwords stay out of the command line
    start = ["docker", "compose", "up", "-d", "--wait", t.service]
    inner = f'export PATH="$PATH:{t.path_extra}"; ' if t.path_extra else ""
    exec_cmd = ["docker", "compose", "exec", "-T", "-w", f"/lab/{t.fmt}/{dataset}",
                *[arg for key in settings for arg in ("-e", key)],
                t.service, "bash", "-c", f"{inner}./{t.script}"]
    return start, exec_cmd, settings


def find_bash() -> str:
    """bash for --host. On Windows prefer Git Bash: a plain `bash` may be the WSL launcher in System32."""
    candidates = [shutil.which("bash")]
    if sys.platform == "win32":
        candidates = [str(Path(base) / "Git" / "bin" / "bash.exe")
                      for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")) if base]
        candidates.append(shutil.which("bash"))
    for c in candidates:
        if c and Path(c).exists() and "system32" not in c.lower():
            return c
    raise RuntimeError("bash not found (Git Bash on Windows, or any bash) - needed to run the generated load scripts")


def load(targets: list[str], dataset: str, host: bool = False, data_dir: Path = DATA_DIR, dry_run: bool = False) -> int:
    env = parse_env()
    names = list(TARGETS) if "all" in targets else targets
    for name in names:
        t = TARGETS[name]
        script = data_dir / t.fmt / dataset / t.script
        if not script.exists():
            print(f"{name}: {script} not found - run `generator generate --formats {name}` first", file=sys.stderr)
            return 2
    if not host and not shutil.which("docker"):
        print("docker not found. Install Docker, or use --host to load with clients on this machine.", file=sys.stderr)
        return 2
    if not host and env.get("ACCEPT_EULA", "N") != "Y" and any(n == "sqlserver" for n in names):
        print("note: ACCEPT_EULA is not Y in .env; the SQL Server container will not start until you accept the EULA.",
              file=sys.stderr)

    for name in names:
        t = TARGETS[name]
        print(f"==> {name} ({'on this machine, URL from .env' if host else 'in the ' + t.service + ' container'})")
        if host:
            cmd, run_env = [find_bash(), str(data_dir / t.fmt / dataset / t.script)], os.environ.copy()
            if dry_run:
                print("   ", " ".join(cmd))
                continue
        else:
            start, cmd, settings = docker_commands(name, env, dataset)
            run_env = {**os.environ, **settings}
            if dry_run:
                print("   ", " ".join(start))
                print("   ", " ".join(cmd))
                continue
            result = subprocess.run(start, cwd=ROOT)
            if result.returncode:
                return result.returncode
        result = subprocess.run(cmd, cwd=ROOT, env=run_env)
        if result.returncode:
            print(f"{name}: load failed (exit {result.returncode})", file=sys.stderr)
            return result.returncode
    return 0
