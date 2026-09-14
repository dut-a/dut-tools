#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-.}"
REPO="$(cd "$REPO" && pwd)"
TOOL="$REPO/tools/deploy-pack"
SCRIPT="$TOOL/scripts/closeout.sh"
MK="$REPO/mk/deploy-pack.inc"
ROOT_MAKEFILE="$REPO/Makefile"
VERSION="$TOOL/VERSION"
DOCS="$TOOL/docs"
MANIFESTS="$DOCS/manifests"

[[ -d "$TOOL" ]] || { echo "FAIL: deploy-pack tool not found at $TOOL" >&2; exit 2; }
mkdir -p "$TOOL/scripts" "$REPO/mk" "$MANIFESTS"

cat > "$SCRIPT" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  closeout.sh prepare [--ref REF] [--out-dir DIR] [--ttl MINUTES] [--language php|python]
  closeout.sh finish --evidence FILE [--session DIR]
  closeout.sh status [--session DIR]

Safe two-phase Git-aware deployment closeout.

prepare
  Requires the tracked/index Git state to be clean, packages the current deployment,
  verifies it locally, issues a verifier identity, generates a signed remote verifier,
  and writes session metadata outside the repository.

finish
  Ingests signed evidence returned from production, marks the exact prepared Git ref,
  then runs baseline/history/history-verify/deploy-status gates.

The deployment/upload itself remains an explicit operator action. This script never
pretends a local command proves files reached production.
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
EOS
chmod +x "$SCRIPT"

# Append idempotent Make integration without disturbing existing deploy-pack fragment content.
touch "$MK"
python3 - "$MK" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
start='# BEGIN DEPLOY-PACK-CLOSEOUT-AUTOMATION-01'
end='# END DEPLOY-PACK-CLOSEOUT-AUTOMATION-01'
block=r'''# BEGIN DEPLOY-PACK-CLOSEOUT-AUTOMATION-01
.PHONY: deploy-prepare deploy-closeout deploy-closeout-status

DEPLOY_PACK_CLOSEOUT_SCRIPT ?= tools/deploy-pack/scripts/closeout.sh

# Prepare archive + signed production verifier in a sibling closeout directory.
# Optional: REF=<git-ref> TTL=60 LANGUAGE=php OUT_DIR=/path/to/session
deploy-prepare:
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) prepare \
		--ref "$${REF:-HEAD}" \
		--ttl "$${TTL:-60}" \
		--language "$${LANGUAGE:-php}" \
		$${OUT_DIR:+--out-dir "$$OUT_DIR"}

# Finish after production returns signed evidence.
# Required: EVIDENCE=/path/to/signed-evidence.json
# Optional: SESSION=/path/to/deploy-pack-closeout-<ref>
deploy-closeout:
	@test -n "$(EVIDENCE)" || { echo "ERROR: EVIDENCE=/path/to/signed-evidence.json is required" >&2; exit 2; }
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) finish \
		--evidence "$(EVIDENCE)" \
		$${SESSION:+--session "$$SESSION"}

deploy-closeout-status:
	@$(DEPLOY_PACK_CLOSEOUT_SCRIPT) status $${SESSION:+--session "$$SESSION"}
# END DEPLOY-PACK-CLOSEOUT-AUTOMATION-01
'''
if start in s:
    a=s.index(start); b=s.index(end,a)+len(end)
    s=s[:a]+block.rstrip()+s[b:]
else:
    if s and not s.endswith('\n'): s+='\n'
    s+='\n'+block
p.write_text(s)
PY

# Ensure the canonical deploy-pack fragment is reachable from the root Makefile.
# Existing dut-tools integrations normally already contain this include, but older
# checkouts may predate the convention. Add it only when deploy-pack.inc is not
# referenced anywhere in the root Makefile.
[[ -f "$ROOT_MAKEFILE" ]] || { echo "FAIL: root Makefile not found at $ROOT_MAKEFILE" >&2; exit 2; }
python3 - "$ROOT_MAKEFILE" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
s = p.read_text()
# A direct reference (include/-include or another expression naming the fragment)
# is sufficient. Do not duplicate it.
if re.search(r'(?m)^\s*-?include\s+.*mk/deploy-pack\.inc(?:\s|$)', s) or 'mk/deploy-pack.inc' in s:
    raise SystemExit(0)
if s and not s.endswith('\n'):
    s += '\n'
s += '\n# deploy-pack integration\n-include mk/deploy-pack.inc\n'
p.write_text(s)
PY

# Version metadata.
printf '1.14.0\n' > "$VERSION"
python3 - "$TOOL" <<'PY'
from pathlib import Path
import re,sys
root=Path(sys.argv[1])
for rel in ('src/deploy_pack/__init__.py','pyproject.toml'):
    p=root/rel
    if not p.exists(): continue
    s=p.read_text()
    s=re.sub(r'(?m)(__version__\s*=\s*")[^"]+("?)',r'\g<1>1.14.0\2',s)
    s=re.sub(r'(?m)^(version\s*=\s*")[^"]+("\s*)$',r'\g<1>1.14.0\2',s)
    p.write_text(s)
PY

cat > "$DOCS/DEPLOY-PACK-CLOSEOUT-AUTOMATION-01.md" <<'MD'
# DEPLOY-PACK-CLOSEOUT-AUTOMATION-01

Release: **1.14.0**

## Goal

Reduce routine Git-aware deployment closeout to two local operator commands without weakening the trust model or pretending that a local process proves a remote deployment occurred.

## Workflow

### 1. Prepare

```sh
make deploy-prepare
```

This performs, in order:

1. refuses tracked/index dirt so the deployed bytes have a canonical Git commit;
2. `deploy-pack inspect`;
3. `deploy-pack pack` into a sibling `deploy-pack-closeout-<ref>/` directory;
4. local archive verification;
5. verifier identity issuance;
6. signed remote-verifier generation;
7. durable session metadata creation outside the repository;
8. prints the exact production verifier command and finish command.

The operator then deploys the archive contents and runs the generated verifier on production. That remote boundary remains explicit because deploy-pack cannot infer that cPanel/shared-hosting upload occurred successfully.

### 2. Finish

After bringing the production signed-evidence JSON back locally:

```sh
make deploy-closeout EVIDENCE=/path/to/deploy-<ref>.signed-evidence.json
```

This performs:

1. signed evidence ingestion;
2. public-key binding;
3. normalization;
4. `deploy-pack mark` of the exact prepared commit;
5. baseline check;
6. recent history display;
7. history-chain verification;
8. deployment status gate;
9. session transition from `prepared` to `closed`.

If multiple prepared sessions exist, specify the intended one:

```sh
make deploy-closeout \
  EVIDENCE=/path/to/evidence.json \
  SESSION=../deploy-pack-closeout-<ref>
```

## Safety properties

- No automatic closeout from a tracked-dirty or staged-dirty tree.
- Generated archives/verifiers/evidence remain outside the project repository by default.
- No `--unsafe-no-evidence` path.
- No remote upload is claimed or inferred.
- The prepared Git commit is persisted in session metadata and is the only ref the finish phase marks.
- Existing signed evidence, replay, verifier lifecycle, transaction, ledger, and history protections remain authoritative.

## Make targets

```text
make deploy-prepare
make deploy-closeout EVIDENCE=<signed-evidence.json>
make deploy-closeout-status
```

Optional prepare variables: `REF`, `TTL`, `LANGUAGE`, `OUT_DIR`.
Optional finish/status variable: `SESSION`.
MD

cat > "$MANIFESTS/DEPLOY-PACK-CLOSEOUT-AUTOMATION-01-MANIFEST.json" <<'JSON'
{
  "increment": "DEPLOY-PACK-CLOSEOUT-AUTOMATION-01",
  "releaseVersion": "1.14.0",
  "interface": {
    "prepare": "make deploy-prepare",
    "finish": "make deploy-closeout EVIDENCE=<signed-evidence.json>",
    "status": "make deploy-closeout-status"
  },
  "properties": {
    "twoPhase": true,
    "remoteBoundaryExplicit": true,
    "requiresCanonicalCommittedRef": true,
    "sessionOutsideRepositoryByDefault": true,
    "unsafeEvidenceBypass": false
  }
}
JSON

echo "== shell syntax =="
bash -n "$SCRIPT"

echo "== make integration =="
grep -Eq '^[[:space:]]*-?include[[:space:]]+.*mk/deploy-pack\.inc([[:space:]]|$)' "$ROOT_MAKEFILE" || {
  echo "FAIL: root Makefile does not include mk/deploy-pack.inc" >&2
  exit 2
}
make -C "$REPO" -n deploy-closeout-status >/dev/null

echo "== closeout help =="
(cd "$REPO" && "$SCRIPT" --help >/dev/null)

echo "== full deploy-pack regression =="
make -C "$REPO" test TOOL=deploy-pack

echo "DEPLOY-PACK-CLOSEOUT-AUTOMATION-01-V2: PASS"
