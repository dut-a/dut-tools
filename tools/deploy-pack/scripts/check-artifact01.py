#!/usr/bin/env python3
from __future__ import annotations
import json, os, pathlib, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'docs/manifests/DEPLOY-PACK-ARTIFACT-01-TEST-EVIDENCE.json'
def run(cmd,env):
    t=time.monotonic(); p=subprocess.run(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True); return p,round(time.monotonic()-t,3)
def main():
    env=os.environ.copy(); env['PYTHONPATH']=str(ROOT/'src')
    commands=[
        ('focused-tests',[sys.executable,'-m','unittest','-v','tests.test_artifact01_built_artifact']),
        ('compileall',[sys.executable,'-m','compileall','-q','src','tests']),
        ('cli-help',[sys.executable,'-m','deploy_pack.cli','artifact','--help']),
        ('version',[sys.executable,'-m','deploy_pack.cli','--version']),
    ]
    checks=[]; failed=False
    for name,cmd in commands:
        p,sec=run(cmd,env); checks.append({'name':name,'status':'PASS' if p.returncode==0 else 'FAIL','seconds':sec,'output':p.stdout[-12000:]}); failed |= p.returncode != 0
    result={'gate':'DEPLOY-PACK-ARTIFACT-01','version':'1.10.0','status':'FAIL' if failed else 'PASS','checks':checks}
    EVIDENCE.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(f"DEPLOY-PACK-ARTIFACT-01: {result['status']}")
    for c in checks:
        print(f"  {c['name']:<14} {c['status']} ({c['seconds']}s)")
        if c['status']=='FAIL': print(c['output'],file=sys.stderr)
    print(f"  evidence       {EVIDENCE.relative_to(ROOT)}")
    return 1 if failed else 0
if __name__=='__main__': raise SystemExit(main())
