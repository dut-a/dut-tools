#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m pip install --user "$SCRIPT_DIR"
printf '%s\n' 'Installed deploy-pack.'
