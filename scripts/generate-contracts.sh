#!/usr/bin/env bash
# Regenerate the OpenAPI snapshot in packages/contracts from the live FastAPI app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
cd "$ROOT/apps/api"
"$PYTHON" - <<'PY'
import json
import pathlib
from app.main import app

spec = app.openapi()
out = pathlib.Path("../../packages/contracts/openapi.json")
out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {out} ({len(spec.get('paths', {}))} paths)")
PY
