"""Bash snippet shared by the generated load scripts: read the repo-root ``.env``.

The scripts live in ``data/<format>/<dataset>/`` so the repo root is three levels up. Variables that are
already set in the environment win over the file, so ``POSTGRES_PORT=5433 ./load.sh`` still works. The file
format is plain ``KEY=value`` lines (comments on their own line, no inline comments, optional quotes).
"""

ENV_LOADER = r"""# Read the repo-root .env; variables already set in the environment win over it.
ENV_FILE="${ENV_FILE:-../../../.env}"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|\#*|' '*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in ''|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    if [ -z "${!key+x}" ]; then export "$key=$value"; fi
  done < "$ENV_FILE"
fi"""
