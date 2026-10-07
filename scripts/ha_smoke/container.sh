#!/usr/bin/env bash
# Runs INSIDE the container started by `run`.  Do not run on the host.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1
REQ=/repo/requirements.txt

# ── venv with the pinned Home Assistant + HA's pytest plugin (cached in the volume) ──
want="$(sed -n 's/^homeassistant==//p' "$REQ" | tr -d '[:space:]')"
have="$(/venv/bin/python -c 'import homeassistant.const as c; print(c.__version__)' 2>/dev/null || true)"
if [ "$have" != "$want" ] || ! /venv/bin/python -c 'import pytest_homeassistant_custom_component' 2>/dev/null; then
  echo "== installing homeassistant==$want and test plugins (first run takes a few minutes)"
  [ -x /venv/bin/python ] || python3 -m venv /venv
  /venv/bin/pip install -q --upgrade pip
  echo "homeassistant==$want" > /tmp/constraint.txt
  /venv/bin/pip install -q -r "$REQ" -c /tmp/constraint.txt \
    pytest pytest-asyncio pytest-homeassistant-custom-component || { echo "INSTALL FAILED"; exit 2; }
fi
/venv/bin/python -c 'import sys, homeassistant.const as c; print("== HA", c.__version__, "on Python", sys.version.split()[0])'

# ── private writable copy of the repo (the mount is read-only) ──
rm -rf /work && mkdir -p /work
tar -C /repo --exclude=.venv --exclude=config --exclude=.git --exclude=.superpowers \
  --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache -cf - . | tar -C /work -xf -
cd /work

# 1. The repo's own unit suite on REAL Home Assistant.  tests/conftest.py is removed because
#    it installs HA/voluptuous stubs (only valid on a bare interpreter) that would shadow the
#    real packages.
echo "== existing unit suite on real HA"
mkdir -p /work/real && cp -r tests /work/real/tests && rm -f /work/real/tests/conftest.py \
  && cp -r custom_components /work/real/
( cd /work/real && /venv/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -5 )
unit_rc=$?

# 2. The smoke tests: real config entry, real coordinator tick, real options flow.
echo "== smoke tests"
mkdir -p /work/smoke && cp scripts/ha_smoke/test_smoke_ha.py /work/smoke/ && cp -r custom_components /work/smoke/
( cd /work/smoke && /venv/bin/python -m pytest test_smoke_ha.py -p no:cacheprovider \
    -o asyncio_mode=auto -v --tb=short 2>&1 | grep -E "PASSED|FAILED|ERROR|^E |passed|failed|error" )
smoke_rc=$?

echo "== unit suite rc=$unit_rc   smoke rc=$smoke_rc"
[ "$unit_rc" -eq 0 ] && [ "$smoke_rc" -eq 0 ]
