#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  closeout.sh prepare [--ref REF] [--out-dir DIR] [--ttl MINUTES] [--language php|python]
  closeout.sh finish --evidence FILE [--session DIR]
  closeout.sh status [--session DIR]

Safe two-phase Git-aware deployment closeout.

WORKFLOW
  1. Commit the exact deployment candidate; automated closeout refuses tracked/staged dirt
     and stray untracked source files.
  2. Run `make deploy-prepare` (or `closeout.sh prepare`).
  3. Deploy the prepared archive contents and the generated signed verifier.
  4. Run the exact verifier command printed by prepare against production.
  5. Bring the resulting signed evidence JSON back locally.
  6. Run `make deploy-closeout EVIDENCE=/path/to/file.json`.
  7. Closeout automatically ingests evidence, marks the prepared ref, then runs baseline,
     history, history-verify, and deploy-status gates.

PREPARE OPTIONS
  --ref REF              Git ref to prepare. Must resolve to current HEAD. Default: HEAD.
  --out-dir DIR          Session/artifact directory. Default: sibling deploy-pack-closeout-<ref>/.
  --ttl MINUTES          Signed verifier identity lifetime. Default: 60.
  --language php|python  Generated production verifier runtime. Default: php.

FINISH OPTIONS
  --evidence FILE        Required signed JSON emitted by the production verifier.
  --session DIR          Prepared closeout session. If omitted, the newest sibling session is used.

STATUS
  `status` prints closeout session metadata only. For deployment governance health use:
      deploy-pack deploy status

OUTPUTS FROM PREPARE
  deploy-<ref>.zip
  deploy-<ref>.verify-signed.<php|py>
  deploy-<ref>.verify-signed.<php|py>.public-key.json
  .deploy-pack-closeout-session.json

SAFETY / TRUST BOUNDARY
  This workflow does not upload files or claim that local preparation proves remote deployment.
  The production verifier is archive-bound and host-cooperative; it is not hostile-host or
  hardware attestation. Remove the signed verifier from production after evidence retrieval.
  Keep closeout artifacts outside the repository deployment surface.

EXAMPLES
  make deploy-prepare
  make deploy-prepare REF=HEAD TTL=60 LANGUAGE=php
  make deploy-closeout EVIDENCE=../deploy-pack-closeout-abc/deploy-abc.signed-evidence.json
  make deploy-closeout-status

For low-level command semantics use:
  deploy-pack remote-verifier --help
  deploy-pack ingest-signed-remote-evidence --help
  deploy-pack mark --help
  deploy-pack reconcile-baseline --help
EOF
}

die() { echo "ERROR: $*" >&2; exit 2; }

MODE="${1:-}"
[[ -n "$MODE" ]] || { usage; exit 2; }
if [[ "$MODE" == "-h" || "$MODE" == "--help" ]]; then usage; exit 0; fi
shift || true

command -v git >/dev/null 2>&1 || die "git is required"
command -v deploy-pack >/dev/null 2>&1 || die "deploy-pack is not on PATH"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "run from inside a Git repository"
cd "$ROOT"

REF="HEAD"
OUT_DIR=""
TTL="60"
LANGUAGE="php"
EVIDENCE=""
SESSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="${2:?missing value for --ref}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:?missing value for --out-dir}"; shift 2 ;;
    --ttl) TTL="${2:?missing value for --ttl}"; shift 2 ;;
    --language) LANGUAGE="${2:?missing value for --language}"; shift 2 ;;
    --evidence) EVIDENCE="${2:?missing value for --evidence}"; shift 2 ;;
    --session) SESSION="${2:?missing value for --session}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$LANGUAGE" in php|python) ;; *) die "--language must be php or python" ;; esac
[[ "$TTL" =~ ^[0-9]+$ ]] || die "--ttl must be an integer"

state_get() {
  local file="$1" key="$2"
  python3 - "$file" "$key" <<'PY'
import json,sys
p,key=sys.argv[1:]
with open(p,encoding='utf-8') as f: d=json.load(f)
v=d
for part in key.split('.'):
    v=v[part]
print(v)
PY
}

latest_session() {
  local parent
  parent="$(dirname "$ROOT")"
  python3 - "$parent" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
items=[]
for p in root.glob('deploy-pack-closeout-*'):
    state=p/'.deploy-pack-closeout-session.json'
    if state.is_file(): items.append((state.stat().st_mtime,p))
if not items: raise SystemExit(1)
print(max(items)[1])
PY
}

case "$MODE" in
  prepare)
    # A closeout baseline must name a Git commit that represents what is being deployed.
    # Ignore untracked operational state, but reject tracked/index dirt by default.
    git diff --quiet || die "tracked working-tree changes exist; commit/stash them before automated closeout"
    git diff --cached --quiet || die "staged but uncommitted changes exist; commit/stash them before automated closeout"

    UNTRACKED="$(git ls-files --others --exclude-standard | grep -Ev '^\.deploy-pack($|[-.])' || true)"
    [[ -z "$UNTRACKED" ]] || {
      printf 'ERROR: untracked source files exist; commit, ignore, or move them before automated closeout:\n%s\n' "$UNTRACKED" >&2
      exit 2
    }

    RESOLVED="$(git rev-parse "${REF}^{commit}" 2>/dev/null)" || die "cannot resolve ref: $REF"
    HEAD_RESOLVED="$(git rev-parse 'HEAD^{commit}')"
    [[ "$RESOLVED" == "$HEAD_RESOLVED" ]] || die "--ref must resolve to current HEAD for automated closeout; checkout the intended deployment commit first"
    SHORT="$(git rev-parse --short=12 "$RESOLVED")"
    if [[ -z "$OUT_DIR" ]]; then
      OUT_DIR="$(dirname "$ROOT")/deploy-pack-closeout-$SHORT"
    elif [[ "$OUT_DIR" != /* ]]; then
      OUT_DIR="$ROOT/$OUT_DIR"
    fi
    mkdir -p "$OUT_DIR"
    OUT_DIR="$(cd "$OUT_DIR" && pwd)"

    ARCHIVE="$OUT_DIR/deploy-$SHORT.zip"
    EXT="py"; [[ "$LANGUAGE" == php ]] && EXT="php"
    VERIFIER="$OUT_DIR/deploy-$SHORT.verify-signed.$EXT"
    PUBKEY="$VERIFIER.public-key.json"
    NORMALIZED="$OUT_DIR/deploy-$SHORT.normalized-evidence.json"
    STATE="$OUT_DIR/.deploy-pack-closeout-session.json"

    [[ ! -e "$ARCHIVE" ]] || die "archive already exists: $ARCHIVE"

    echo "== inspect =="
    deploy-pack inspect
    echo "== pack =="
    deploy-pack pack --output "$ARCHIVE"
    echo "== local verify =="
    deploy-pack verify "$ARCHIVE"
    echo "== issue verifier =="
    ISSUE_OUTPUT="$(deploy-pack verifier issue --ttl-minutes "$TTL")"
    printf '%s\n' "$ISSUE_OUTPUT"
    VERIFIER_ID="$(printf '%s\n' "$ISSUE_OUTPUT" | sed -n 's/^Verifier ID[[:space:]]*:[[:space:]]*//p' | tail -1)"
    [[ -n "$VERIFIER_ID" ]] || die "could not parse verifier identity from deploy-pack output"

    echo "== generate signed verifier =="
    deploy-pack remote-verifier \
      "$ARCHIVE" \
      --language "$LANGUAGE" \
      --sign \
      --verifier-id "$VERIFIER_ID" \
      --output "$VERIFIER"

    python3 - "$STATE" "$ROOT" "$RESOLVED" "$ARCHIVE" "$VERIFIER" "$PUBKEY" "$NORMALIZED" "$VERIFIER_ID" "$LANGUAGE" <<'PY'
import json,sys
from datetime import datetime,timezone
state,root,ref,archive,verifier,pubkey,normalized,vid,language=sys.argv[1:]
data={
  'schemaVersion':1,
  'createdAt':datetime.now(timezone.utc).isoformat(),
  'repoRoot':root,
  'ref':ref,
  'archive':archive,
  'verifier':verifier,
  'publicKey':pubkey,
  'normalizedEvidence':normalized,
  'verifierId':vid,
  'language':language,
  'phase':'prepared'
}
with open(state,'w',encoding='utf-8') as f: json.dump(data,f,indent=2); f.write('\n')
PY

    echo
    echo "DEPLOY-PACK CLOSEOUT PREPARE: PASS"
    echo "Ref      : $RESOLVED"
    echo "Archive  : $ARCHIVE"
    echo "Verifier : $VERIFIER"
    echo "Session  : $OUT_DIR"
    echo
    echo "NEXT — deploy the archive contents to production, upload the verifier, then run there:"
    if [[ "$LANGUAGE" == php ]]; then
      echo "  php $(basename "$VERIFIER") --signed-evidence-out deploy-$SHORT.signed-evidence.json"
    else
      echo "  python3 $(basename "$VERIFIER") --signed-evidence-out deploy-$SHORT.signed-evidence.json"
    fi
    echo
    echo "Bring that signed evidence JSON back, then run locally:"
    echo "  make deploy-closeout EVIDENCE=/path/to/deploy-$SHORT.signed-evidence.json SESSION='$OUT_DIR'"
    ;;

  finish)
    if [[ -z "$SESSION" ]]; then SESSION="$(latest_session 2>/dev/null || true)"; fi
    [[ -n "$SESSION" ]] || die "no closeout session found; pass --session DIR"
    [[ "$SESSION" == /* ]] || SESSION="$ROOT/$SESSION"
    STATE="$SESSION/.deploy-pack-closeout-session.json"
    [[ -f "$STATE" ]] || die "session metadata not found: $STATE"
    [[ -n "$EVIDENCE" ]] || die "finish requires --evidence FILE"
    [[ "$EVIDENCE" == /* ]] || EVIDENCE="$ROOT/$EVIDENCE"
    [[ -f "$EVIDENCE" ]] || die "signed evidence not found: $EVIDENCE"

    EXPECTED_ROOT="$(state_get "$STATE" repoRoot)"
    [[ "$EXPECTED_ROOT" == "$ROOT" ]] || die "session belongs to another repository: $EXPECTED_ROOT"
    PREPARED_REF="$(state_get "$STATE" ref)"
    ARCHIVE="$(state_get "$STATE" archive)"
    PUBKEY="$(state_get "$STATE" publicKey)"
    NORMALIZED="$(state_get "$STATE" normalizedEvidence)"
    [[ -f "$ARCHIVE" ]] || die "prepared archive missing: $ARCHIVE"
    [[ -f "$PUBKEY" ]] || die "prepared public-key sidecar missing: $PUBKEY"

    echo "== ingest signed remote evidence =="
    deploy-pack ingest-signed-remote-evidence \
      "$EVIDENCE" \
      "$ARCHIVE" \
      --public-key "$PUBKEY" \
      --output "$NORMALIZED"

    echo "== mark verified deployment =="
    deploy-pack mark "$PREPARED_REF" \
      --archive "$ARCHIVE" \
      --evidence "$NORMALIZED"

    echo "== post-closeout gates =="
    deploy-pack baseline
    deploy-pack history --limit 5
    deploy-pack history-verify
    deploy-pack deploy status

    python3 - "$STATE" "$EVIDENCE" <<'PY'
import json,sys
from datetime import datetime,timezone
p,evidence=sys.argv[1:]
with open(p,encoding='utf-8') as f:d=json.load(f)
d['phase']='closed'
d['closedAt']=datetime.now(timezone.utc).isoformat()
d['signedEvidence']=evidence
with open(p,'w',encoding='utf-8') as f:json.dump(d,f,indent=2);f.write('\n')
PY
    echo
    echo "DEPLOY-PACK CLOSEOUT: PASS"
    echo "Baseline : $PREPARED_REF"
    echo "Session  : $SESSION"
    ;;

  status)
    if [[ -z "$SESSION" ]]; then SESSION="$(latest_session 2>/dev/null || true)"; fi
    [[ -n "$SESSION" ]] || die "no closeout session found; pass --session DIR"
    [[ "$SESSION" == /* ]] || SESSION="$ROOT/$SESSION"
    STATE="$SESSION/.deploy-pack-closeout-session.json"
    [[ -f "$STATE" ]] || die "session metadata not found: $STATE"
    cat "$STATE"
    ;;

  *) usage; exit 2 ;;
esac
