#!/usr/bin/env bash
set -euo pipefail
export GIT_PAGER=cat PAGER=cat GIT_TERMINAL_PROMPT=0

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <dut-tools-repo>" >&2
  exit 2
fi
REPO="$(cd "$1" && pwd)"
TOOL="$REPO/tools/deploy-pack"
PY="$TOOL/.venv/bin/python"
[[ -d "$TOOL" ]] || { echo "FAIL: missing $TOOL" >&2; exit 1; }
[[ -x "$PY" ]] || { echo "FAIL: missing managed runtime $PY" >&2; exit 1; }

python3 - "$TOOL" <<'PY'
from pathlib import Path
import json, re, sys
root=Path(sys.argv[1])
core=root/'src/deploy_pack/core.py'
signed=root/'src/deploy_pack/signed.py'
cli=root/'src/deploy_pack/cli.py'
version=root/'VERSION'
init=root/'src/deploy_pack/__init__.py'
pyproject=root/'pyproject.toml'
for p in (core,signed,cli,version,init,pyproject):
    if not p.exists(): raise SystemExit(f"FAIL: missing {p}")

# 1) Protect all deploy-pack generated verifier/evidence artifacts, including
# custom output names, by both filename and content signature.
s=core.read_text()
if 'unsigned_remote_verifier = (' not in s:
    anchor='''    browser_php = (\n        "$expectedToken=" in text\n        and "hash_equals($expectedToken,$providedToken)" in text\n        and "verificationMethod\'=>\'browser\'" in text\n        and "php-browser" in text\n    )\n    if signed_python or signed_php:\n        return "secret-bearing deploy-pack signed verifier"\n    if browser_php:\n        return "token-bearing deploy-pack browser verifier"\n'''
    repl='''    browser_php = (\n        "$expectedToken=" in text\n        and "hash_equals($expectedToken,$providedToken)" in text\n        and "verificationMethod\'=>\'browser\'" in text\n        and "php-browser" in text\n    )\n    unsigned_remote_verifier = (\n        "DEPLOY-PACK VERIFY: PASS" in text\n        and "remoteDeletions" in text\n        and ("verificationMethod" in text or "verificationScope" in text)\n        and ("MANIFEST=" in text or "$manifest=json_decode(" in text)\n    )\n    if signed_python or signed_php:\n        return "secret-bearing deploy-pack signed verifier"\n    if browser_php:\n        return "token-bearing deploy-pack browser verifier"\n    if unsigned_remote_verifier:\n        return "deploy-pack generated remote verifier"\n'''
    if anchor not in s: raise SystemExit('FAIL: protected-artifact verifier anchor not found')
    s=s.replace(anchor,repl,1)

# Broaden the pre-existing evidence detector to current evidence scope names and
# custom normalized evidence output names.
legacy_evidence = '''            if (\n                value.get("verificationScope") in {"local-archive", "local-extracted", "remote"}\n                and "manifest" in value\n                and "verifiedAt" in value\n            ):\n                return "deploy-pack verification evidence"\n'''
modern_evidence = '''            if (\n                value.get("verificationScope") in {"local-archive", "local-extracted", "archive", "extracted-tree", "remote"}\n                and isinstance(value.get("manifest"), dict)\n                and ("verifiedAt" in value or value.get("result") in {"PASS", "FAIL"})\n                and ("headCommit" in value.get("manifest", {}) or "sha256" in value.get("manifest", {}))\n            ):\n                return "deploy-pack verification evidence"\n'''
if modern_evidence not in s:
    if legacy_evidence in s:
        s=s.replace(legacy_evidence, modern_evidence, 1)
    else:
        anchor='''        if isinstance(value, dict):\n            kind = str(value.get("kind", ""))\n            if kind.startswith("deploy-pack."):\n                return "deploy-pack signed/control artifact"\n'''
        insert=anchor+'''            if (\n                value.get("verificationScope") in {"archive", "extracted-tree", "remote"}\n                and isinstance(value.get("manifest"), dict)\n                and value.get("result") in {"PASS", "FAIL"}\n            ):\n                return "deploy-pack verification evidence"\n'''
        if anchor not in s: raise SystemExit('FAIL: protected-artifact JSON anchor not found')
        s=s.replace(anchor,insert,1)

# 3) Audited baseline reconciliation support.
if 'def validate_baseline_reconciliation(' not in s:
    anchor='''REMOTE_EVIDENCE_SCHEMA_VERSION = 1\n'''
    insert='''def validate_baseline_reconciliation(\n    root: Path,\n    ref: str,\n    evidence_path: Path,\n    *,\n    archive: Path,\n) -> tuple[str | None, str, dict]:\n    """Validate a correction of recorded deployment state without weakening mark invariants.\n\n    Reconciliation is for the case where production already contains the intended bytes but\n    the recorded baseline points at the wrong Git commit. The correcting archive/evidence\n    must be freshly bound to the target commit; old evidence for a different head is rejected.\n    """\n    if archive is None:\n        raise DeployPackError("baseline reconciliation requires --archive")\n    previous_ref = read_baseline(root)\n    if not previous_ref:\n        raise DeployPackError("baseline reconciliation requires an existing recorded baseline")\n    previous_commit = resolve_ref(root, previous_ref)\n    resolved, evidence = validate_mark_evidence(\n        root, ref, evidence_path, archive=archive\n    )\n    if resolved == previous_commit:\n        raise DeployPackError("baseline reconciliation target already matches the recorded baseline")\n    proc = subprocess.run(\n        ["git", "merge-base", "--is-ancestor", previous_commit, resolved],\n        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,\n    )\n    if proc.returncode != 0:\n        raise DeployPackError(\n            "baseline reconciliation target must descend from the currently recorded baseline; "\n            "use the explicit rollback workflow for non-descendant history"\n        )\n    return previous_commit, resolved, evidence\n\n\n'''
    if anchor not in s: raise SystemExit('FAIL: reconciliation insertion anchor not found')
    s=s.replace(anchor,insert+anchor,1)

# Extend history records with explicit reconciliation semantics/reason.
old='''    rollback_target_record: int | None = None,\n    filename: str = LEDGER_FILE,\n) -> dict:\n'''
new='''    rollback_target_record: int | None = None,\n    deployment_kind: str | None = None,\n    reconciliation_reason: str | None = None,\n    filename: str = LEDGER_FILE,\n) -> dict:\n'''
if 'reconciliation_reason: str | None = None' not in s:
    if old not in s: raise SystemExit('FAIL: append history signature anchor not found')
    s=s.replace(old,new,1)

old='''        "deploymentKind": "rollback" if rollback_target_record is not None else "forward",\n'''
new='''        "deploymentKind": deployment_kind or ("rollback" if rollback_target_record is not None else "forward"),\n'''
if new not in s:
    if old not in s: raise SystemExit('FAIL: deploymentKind anchor not found')
    s=s.replace(old,new,1)

if 'record["reconciliation"] = {' not in s:
    anchor='''    if rollback_target_record is not None:\n'''
    insert='''    if record["deploymentKind"] == "reconciliation":\n        reason = (reconciliation_reason or "").strip()\n        if not reason:\n            raise DeployPackError("reconciliation history requires a non-empty reason")\n        record["reconciliation"] = {\n            "reason": reason,\n            "previousRecordedBaseline": previous_baseline,\n            "verifiedTargetCommit": new_baseline_commit,\n        }\n\n'''
    # Only first occurrence after append_deployment_history record construction.
    idx=s.find('def append_deployment_history(')
    pos=s.find(anchor,idx)
    if pos<0: raise SystemExit('FAIL: reconciliation history anchor not found')
    s=s[:pos]+insert+s[pos:]

core.write_text(s)

# 2) Fix signed verifier option parsing in both generated runtimes and add help.
s=signed.read_text()
old='''def main():\n root=Path(sys.argv[1] if len(sys.argv)>1 else ".").resolve(); strict="--strict-permissions" in sys.argv\n if "--signed-evidence-out" not in sys.argv:\n  print("ERROR: --signed-evidence-out is required",file=sys.stderr); return 2\n out=Path(sys.argv[sys.argv.index("--signed-evidence-out")+1]); failures=[]\n'''
new='''def main():\n root=Path("."); strict=False; out=None; positional=[]; i=1\n while i < len(sys.argv):\n  arg=sys.argv[i]\n  if arg in ("-h","--help"):\n   print(f"usage: {Path(sys.argv[0]).name} [ROOT] --signed-evidence-out FILE [--strict-permissions]"); return 0\n  if arg=="--strict-permissions": strict=True; i+=1; continue\n  if arg=="--signed-evidence-out":\n   if i+1>=len(sys.argv): print("ERROR: --signed-evidence-out requires FILE",file=sys.stderr); return 2\n   out=Path(sys.argv[i+1]); i+=2; continue\n  if arg.startswith("-"):\n   print(f"ERROR: unknown option: {arg}",file=sys.stderr); return 2\n  positional.append(arg); i+=1\n if len(positional)>1:\n  print("ERROR: at most one ROOT may be supplied",file=sys.stderr); return 2\n if positional: root=Path(positional[0])\n root=root.resolve()\n if out is None:\n  print("ERROR: --signed-evidence-out is required",file=sys.stderr); return 2\n failures=[]\n'''
if old in s:
    s=s.replace(old,new,1)
elif 'at most one ROOT may be supplied' not in s:
    raise SystemExit('FAIL: Python signed-verifier parser anchor not found')

old='''$root=$argv[1]??'.';$root=realpath($root)?:$root;$strict=in_array('--strict-permissions',$argv,true);$out=null;\nfor($i=2;$i<count($argv);$i++)if($argv[$i]==='--signed-evidence-out'&&isset($argv[$i+1]))$out=$argv[$i+1];\nif($out===null){fwrite(STDERR,"ERROR: --signed-evidence-out is required\\n");exit(2);}$fail=[];\n'''
new='''$root='.';$strict=false;$out=null;$positional=[];\nfor($i=1;$i<count($argv);$i++){\n  $arg=$argv[$i];\n  if($arg==='-h'||$arg==='--help'){fwrite(STDOUT,"usage: ".basename($argv[0])." [ROOT] --signed-evidence-out FILE [--strict-permissions]\\n");exit(0);}\n  if($arg==='--strict-permissions'){$strict=true;continue;}\n  if($arg==='--signed-evidence-out'){if(!isset($argv[$i+1])){fwrite(STDERR,"ERROR: --signed-evidence-out requires FILE\\n");exit(2);}$out=$argv[++$i];continue;}\n  if(str_starts_with($arg,'-')){fwrite(STDERR,"ERROR: unknown option: ".$arg."\\n");exit(2);}\n  $positional[]=$arg;\n}\nif(count($positional)>1){fwrite(STDERR,"ERROR: at most one ROOT may be supplied\\n");exit(2);}\nif(count($positional)===1)$root=$positional[0];$root=realpath($root)?:$root;\nif($out===null){fwrite(STDERR,"ERROR: --signed-evidence-out is required\\n");exit(2);}$fail=[];\n'''
if old in s:
    s=s.replace(old,new,1)
elif 'count($positional)>1' not in s:
    raise SystemExit('FAIL: PHP signed-verifier parser anchor not found')
signed.write_text(s)

# CLI: richer remote-verifier help and reconciliation command.
s=cli.read_text()
if 'reconcile-baseline              Correct a recorded production baseline' not in s:
    s=s.replace(
        '    mark                      Record a verified deployment as deployed.\n',
        '    mark                      Record a verified deployment as deployed.\n'
        '    reconcile-baseline        Correct a recorded production baseline with fresh signed evidence.\n',
        1,
    )
if 'reconcile-baseline' not in s:
    mark_anchor='''    mark.add_argument(\n        "--rollback-to",\n        type=int,\n        help="Record this mark as an intentional rollback to the given history record.",\n    )\n\n'''
    reconcile_parser='''    reconcile = sub.add_parser(\n        "reconcile-baseline",\n        help="Correct a recorded production baseline using fresh target-bound signed evidence.",\n    )\n    reconcile.add_argument("ref", help="Git ref/commit that exactly represents current production bytes.")\n    reconcile.add_argument("--archive", required=True, help="Correction archive whose manifest headCommit resolves to REF.")\n    reconcile.add_argument("--evidence", required=True, help="Fresh normalized signed remote evidence for --archive.")\n    reconcile.add_argument("--reason", required=True, help="Operator/audit reason for correcting the recorded baseline.")\n\n'''
    if mark_anchor not in s: raise SystemExit('FAIL: reconcile parser anchor not found')
    s=s.replace(mark_anchor,mark_anchor+reconcile_parser,1)

    branch_anchor='''        if args.command == "mark":\n'''
    branch='''        if args.command == "reconcile-baseline":\n            from .lifecycle import REPLAY_STATE_FILE\n            from .core import LEDGER_FILE\n            with repository_lock(root):\n                recovered = recover_mark_transaction(root)\n                if recovered:\n                    print("WARNING: recovered an incomplete prior mark transaction before continuing.")\n                evidence_path = Path(args.evidence).expanduser().resolve()\n                archive_path = Path(args.archive).expanduser().resolve()\n                previous_commit, resolved, evidence = validate_baseline_reconciliation(\n                    root, args.ref, evidence_path, archive=archive_path\n                )\n                if not evidence_is_signed_remote(evidence):\n                    raise DeployPackError("baseline reconciliation requires signed remote evidence")\n                replay_key = assert_signed_evidence_usable(root, evidence)\n                tx_paths = [root / BASELINE_FILE, root / LEDGER_FILE, root / REPLAY_STATE_FILE]\n                evidence_for_history = dict(evidence)\n                evidence_for_history["trustMode"] = "signed-remote"\n                begin_mark_transaction(\n                    root, tx_paths,\n                    {"command":"reconcile-baseline","ref":args.ref,"unsafe":False,"evidence":str(evidence_path)},\n                )\n                try:\n                    write_baseline(root, args.ref)\n                    append_deployment_history(\n                        root,\n                        previous_baseline=previous_commit,\n                        new_baseline_ref=args.ref,\n                        new_baseline_commit=resolved,\n                        evidence_path=evidence_path,\n                        evidence=evidence_for_history,\n                        archive=archive_path,\n                        unsafe=False,\n                        deployment_kind="reconciliation",\n                        reconciliation_reason=args.reason,\n                    )\n                    consume_signed_evidence(\n                        root, replay_key, evidence_path=evidence_path,\n                        marked_ref=args.ref, marked_commit=resolved,\n                    )\n                    commit_mark_transaction(root)\n                except Exception:\n                    recover_mark_transaction(root)\n                    raise\n                print("DEPLOY-PACK BASELINE RECONCILIATION: PASS")\n                print(f"Previous recorded baseline : {previous_commit}")\n                print(f"Corrected baseline         : {resolved}")\n                print(f"Evidence                   : {evidence_path}")\n                print(f"Archive                    : {archive_path}")\n                print(f"Reason                     : {args.reason}")\n                print("Deployment history         : appended (reconciliation)")\n                return 0\n\n'''
    if branch_anchor not in s: raise SystemExit('FAIL: reconcile command branch anchor not found')
    s=s.replace(branch_anchor,branch+branch_anchor,1)

# Import validation helper into CLI core import list by appending a small explicit import.
if 'from .core import validate_baseline_reconciliation' not in s:
    # Safe standalone import avoids rewriting the large grouped import block.
    first_import='from .core import ('
    idx=s.find(first_import)
    if idx<0: raise SystemExit('FAIL: core import anchor not found')
    # place after closing grouped import
    close=s.find('\n)', idx)
    if close<0: raise SystemExit('FAIL: grouped core import close not found')
    close+=2
    s=s[:close]+'\nfrom .core import validate_baseline_reconciliation'+s[close:]

# Improve generated-verifier operator instructions.
s=s.replace('print("Remote usage: add --signed-evidence-out <file.json>")',
'''print(f"Remote usage: {args.language == 'php' and 'php' or 'python3'} {path.name} --signed-evidence-out <file.json>")\n                print("Optional deployment root: place ROOT before or after options; default is current directory.")''')
cli.write_text(s)

# Version 1.13.0: new audited reconciliation command plus hardening behavior.
version.write_text('1.13.0\n')
for p in (init, pyproject):
    t=p.read_text()
    t=re.sub(r'(?m)(__version__\s*=\s*")1\.(?:11|12)\.\d+("?)', r'\g<1>1.13.0\2', t)
    t=re.sub(r'(?m)^(version\s*=\s*")1\.(?:11|12)\.\d+("\s*)$', r'\g<1>1.13.0\2', t)
    p.write_text(t)

# Docs + manifest.
docs=root/'docs'; manifests=docs/'manifests'; manifests.mkdir(parents=True,exist_ok=True)
(docs/'DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01.md').write_text('''# DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01\n\nRelease: 1.13.0\n\n## Scope\n\n1. Generated verifier/evidence artifacts are protected by content signature even when the operator chooses a custom filename.\n2. Signed PHP/Python verifier CLIs parse options independently of ROOT; ROOT defaults to `.` and `--help` documents usage.\n3. `deploy-pack reconcile-baseline` provides an audited correction path when production bytes are already correct but the recorded Git baseline is wrong.\n\n## Reconciliation invariant\n\nReconciliation is **not** an evidence bypass. It requires a correction archive and fresh signed remote evidence whose manifest `headCommit` equals the requested ref. The target must descend from the current recorded baseline. Replay protection, repository locking, transactional state mutation, history hashing, and evidence consumption remain active.\n\nExample:\n\n```sh\ndeploy-pack reconcile-baseline 444b22c \\\n  --archive baseline-correction-444b22c.zip \\\n  --evidence baseline-correction-444b22c-evidence.json \\\n  --reason "Production bytes were deployed from a dirty tree and later committed exactly as 444b22c; earlier closeout recorded the pre-commit HEAD."\n```\n''')
(manifests/'DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01-MANIFEST.json').write_text(json.dumps({
  'increment':'DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01','releaseVersion':'1.13.0',
  'changes':['generated-artifact-content-protection','signed-verifier-cli-parser','audited-baseline-reconciliation'],
  'reconciliation':{'requiresArchive':True,'requiresFreshSignedRemoteEvidence':True,'requiresReason':True,'requiresDescendantTarget':True,'unsafeBypass':False}
},indent=2)+"\n")
PY

# Focused regression tests added as a separate file so existing suites remain untouched.
cat > "$TOOL/tests/test_git_closeout_harden01.py" <<'PY'
import json, subprocess, tempfile, unittest
from pathlib import Path

from unittest.mock import patch

from deploy_pack.core import DeployPackError, protected_artifact_reason, validate_baseline_reconciliation
from deploy_pack.signed import php_signed_verifier, python_signed_verifier


class GitCloseoutHarden01Tests(unittest.TestCase):
    def test_custom_named_unsigned_verifier_is_protected_by_content(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); p=root/'whatever.php'
            p.write_text("<?php $manifest=json_decode('{}',true); /* DEPLOY-PACK VERIFY: PASS remoteDeletions verificationMethod */")
            self.assertEqual(protected_artifact_reason(root,p.name), 'deploy-pack generated remote verifier')

    def test_custom_named_normalized_evidence_is_protected_by_content(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); p=root/'my-proof.json'
            p.write_text(json.dumps({'schemaVersion':1,'result':'PASS','verificationScope':'remote','manifest':{'headCommit':'abc','sha256':'def'}}))
            self.assertEqual(protected_artifact_reason(root,p.name), 'deploy-pack verification evidence')

    def test_python_signed_verifier_accepts_option_before_root(self):
        manifest={'schemaVersion':2,'baselineRef':'x','baselineCommit':'a','headCommit':'b','files':[],'remoteDeletions':[]}
        src=python_signed_verifier(manifest,b'0'*32,b'1'*32,{'verifierId':'v'})
        self.assertIn('root=Path(".")',src)
        self.assertIn('usage:',src)
        self.assertIn('at most one ROOT may be supplied',src)

    def test_reconciliation_requires_descendant_target(self):
        evidence={'verificationScope':'remote'}
        fake_proc=type('P',(),{'returncode':1})()
        with patch('deploy_pack.core.read_baseline',return_value='old'), \
             patch('deploy_pack.core.resolve_ref',side_effect=lambda _r, ref: {'old':'a','new':'b'}[ref]), \
             patch('deploy_pack.core.validate_mark_evidence',return_value=('b',evidence)), \
             patch('deploy_pack.core.subprocess.run',return_value=fake_proc):
            with self.assertRaisesRegex(DeployPackError,'must descend'):
                validate_baseline_reconciliation(Path('.'),'new',Path('evidence.json'),archive=Path('archive.zip'))

    def test_reconciliation_returns_current_and_target_when_descendant(self):
        evidence={'verificationScope':'remote'}
        fake_proc=type('P',(),{'returncode':0})()
        with patch('deploy_pack.core.read_baseline',return_value='old'), \
             patch('deploy_pack.core.resolve_ref',side_effect=lambda _r, ref: {'old':'a','new':'b'}[ref]), \
             patch('deploy_pack.core.validate_mark_evidence',return_value=('b',evidence)), \
             patch('deploy_pack.core.subprocess.run',return_value=fake_proc):
            self.assertEqual(
                validate_baseline_reconciliation(Path('.'),'new',Path('evidence.json'),archive=Path('archive.zip')),
                ('a','b',evidence),
            )

    def test_php_signed_verifier_accepts_option_before_root(self):
        manifest={'schemaVersion':2,'baselineRef':'x','baselineCommit':'a','headCommit':'b','files':[],'remoteDeletions':[]}
        src=php_signed_verifier(manifest,b'0'*32,b'1'*32,{'verifierId':'v'})
        self.assertIn("$root='.'",src)
        self.assertIn('usage:',src)
        self.assertIn('count($positional)>1',src)
        php=subprocess.run(['bash','-lc','command -v php >/dev/null'],check=False)
        if php.returncode==0:
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/'verify.php'; p.write_text(src)
                proc=subprocess.run(['php',str(p),'--help'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                self.assertEqual(proc.returncode,0,proc.stdout)
                self.assertIn('[ROOT] --signed-evidence-out FILE',proc.stdout)


if __name__=='__main__': unittest.main()
PY

cat > "$TOOL/docs/manifests/DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01-TEST-EVIDENCE.json" <<'JSON'
{
  "increment": "DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01",
  "releaseVersion": "1.13.0",
  "focusedGate": "tests.test_git_closeout_harden01",
  "fullSuiteCommand": "make test TOOL=deploy-pack"
}
JSON

echo "== syntax =="
"$PY" -m py_compile \
  "$TOOL/src/deploy_pack/core.py" \
  "$TOOL/src/deploy_pack/signed.py" \
  "$TOOL/src/deploy_pack/cli.py" \
  "$TOOL/tests/test_git_closeout_harden01.py"

echo "== focused gate =="
(cd "$TOOL" && PYTHONPATH=src "$PY" -m unittest -v tests.test_git_closeout_harden01)

echo "== full deploy-pack regression =="
make -C "$REPO" test TOOL=deploy-pack

echo "DEPLOY-PACK-GIT-CLOSEOUT-HARDEN-01: PASS"
