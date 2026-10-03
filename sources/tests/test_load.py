"""`.env` handling and `generator load` command construction (no Docker needed)."""
import re
import subprocess

import pytest

from generator.loader import TARGETS, container_env, docker_commands, find_bash, parse_env
from generator.writers.envfile import ENV_LOADER


def test_parse_env(tmp_path):
    f = tmp_path / ".env"
    f.write_bytes(b'# comment\r\nA=1\r\n\r\nB="two words"\r\nC=has=equals!\r\nEMPTY=\r\n  # indented comment\r\nD=\'q\'\r\nnot a pair\r\n')
    assert parse_env(f) == {"A": "1", "B": "two words", "C": "has=equals!", "EMPTY": "", "D": "q"}


def test_parse_env_missing_file(tmp_path):
    assert parse_env(tmp_path / "nope") == {}


def test_container_env_uses_internal_ports_and_env_credentials():
    env = {"POSTGRES_PORT": "5999", "POSTGRES_PASSWORD": "pw", "SQLSERVER_PORT": "1999", "SQLSERVER_PASSWORD": "sa-pw",
           "MONGO_USER": "u", "MONGO_PASSWORD": "p", "MONGO_PORT": "29999"}
    pg = container_env("postgresql", env, "fruit_juice")
    assert pg["POSTGRES_PORT"] == "5432" and pg["POSTGRES_PASSWORD"] == "pw"       # host-side port is not used inside
    ms = container_env("sqlserver", env, "fruit_juice")
    assert ms["SQLSERVER_PORT"] == "1433" and ms["SQLSERVER_DATA_DIR"] == "/lab/csv/fruit_juice"
    assert ms["SQLSERVER_SERVER"] == "" and ms["SQLSERVER_PASSWORD"] == "sa-pw"
    mongo = container_env("mongodb", env, "fruit_juice")
    assert mongo["MONGO_URI"] == "mongodb://u:p@localhost:27017/?authSource=admin"


def test_docker_commands():
    secrets = {"POSTGRES_PASSWORD": "pg-secret", "SQLSERVER_PASSWORD": "ms-secret", "MONGO_PASSWORD": "mongo-secret"}
    for name, target in TARGETS.items():
        start, cmd, settings = docker_commands(name, secrets, "fruit_juice")
        assert start[:4] == ["docker", "compose", "up", "-d"] and start[-1] == target.service
        assert cmd[:4] == ["docker", "compose", "exec", "-T"]
        assert f"/lab/{target.fmt}/fruit_juice" in cmd and target.service in cmd
        assert cmd[-1].endswith(f"./{target.script}")
        # secrets are forwarded by name, never written into the command line
        assert not any(secret in " ".join(cmd) for secret in secrets.values())
    assert "/opt/mssql-tools18/bin" in docker_commands("sqlserver", {}, "fruit_juice")[1][-1]


def _bash_or_skip():
    try:
        return find_bash()
    except RuntimeError:
        pytest.skip("bash not available")


def _run_loader(tmp_path, env_text, environ=None):
    bash = _bash_or_skip()
    (tmp_path / ".env").write_bytes(env_text)
    script = tmp_path / "t.sh"
    script.write_text(f'set -eu\nENV_FILE="{(tmp_path / ".env").as_posix()}"\n{ENV_LOADER.split(chr(10), 1)[1]}\n'
                      'echo "A=$A|B=$B|C=$C|E=${E-unset}"\n', encoding="utf-8", newline="\n")
    out = subprocess.run([bash, script.as_posix()], capture_output=True, text=True, env=environ)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_env_loader_reads_file_and_environment_wins(tmp_path):
    import os
    result = _run_loader(tmp_path, b'# c\r\nA=from-file\r\nB="quoted value"\r\nC=pw=with=equals!\r\nE=\r\nbad key=1\r\n',
                         environ={**os.environ, "A": "from-shell"})
    assert result == "A=from-shell|B=quoted value|C=pw=with=equals!|E="


def test_generated_scripts_are_wired_to_env(tmp_path):
    from conftest import make_cfg
    from generator.pipeline import generate
    cfg, out = make_cfg(tmp_path, "2013-01-31", formats=["postgresql", "sqlserver", "mongodb"])
    generate(cfg, out, log=lambda _: None)
    root = tmp_path
    pg = (root / "postgresql" / "fruit_juice" / "load.sh").read_text(encoding="utf-8")
    ms = (root / "sqlserver" / "fruit_juice" / "load.sh").read_text(encoding="utf-8")
    mongo = (root / "mongodb" / "fruit_juice" / "import.sh").read_text(encoding="utf-8")
    for script, names in ((pg, ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DATABASE"]),
                          (ms, ["SQLSERVER_HOST", "SQLSERVER_PORT", "SQLSERVER_USER", "SQLSERVER_PASSWORD",
                                "SQLSERVER_DATABASE", "SQLSERVER_SERVER", "SQLSERVER_DATA_DIR"]),
                          (mongo, ["MONGO_URI", "MONGO_HOST", "MONGO_PORT", "MONGO_USER", "MONGO_PASSWORD", "MONGO_DATABASE"])):
        assert "ENV_FILE" in script and not re.search(r"@@[A-Z]+@@", script)
        assert all(n in script for n in names), [n for n in names if n not in script]
    assert "$(CodePageOption)" in (root / "sqlserver" / "fruit_juice" / "02_load.sql").read_text(encoding="utf-8")
    assert not re.search(r"@@[A-Z]+@@", (root / "mongodb" / "fruit_juice" / "README.md").read_text(encoding="utf-8"))
    assert "sales" in mongo
    bash = _bash_or_skip()
    for path in (root / "postgresql" / "fruit_juice" / "load.sh", root / "sqlserver" / "fruit_juice" / "load.sh",
                 root / "mongodb" / "fruit_juice" / "import.sh"):
        assert subprocess.run([bash, "-n", path.as_posix()], capture_output=True).returncode == 0, path
