#!/usr/bin/env python3
from __future__ import annotations
import json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = sorted((ROOT / 'tests').glob('test_*.py'))
BATCH_SIZE = 8

def run(cmd, *, env):
    started=time.monotonic()
    p=subprocess.run(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    return p, time.monotonic()-started

def parse_test_count(output: str) -> int:
    for line in reversed(output.splitlines()):
        if line.startswith('Ran ') and ' test' in line:
            try: return int(line.split()[1])
            except Exception: return 0
    return 0

def main() -> int:
    env=os.environ.copy(); env['PYTHONPATH']=str(ROOT/'src')
    batches=[]; failures=[]; total=0; elapsed=0.0
    for start in range(0,len(TESTS),BATCH_SIZE):
        paths=TESTS[start:start+BATCH_SIZE]
        modules=[f'tests.{p.stem}' for p in paths]
        p,d=run([sys.executable,'-m','unittest','-v',*modules],env=env); elapsed+=d
        count=parse_test_count(p.stdout); total+=count
        entry={'batch':len(batches)+1,'modules':modules,'tests':count,'seconds':round(d,3),'status':'PASS' if p.returncode==0 else 'FAIL'}
        batches.append(entry)
        if p.returncode:
            failures.append({'batch':entry['batch'],'output':p.stdout[-12000:]}); break
    checks=[]
    for label,cmd in [('compileall',[sys.executable,'-m','compileall','-q','src','tests']),('version',[sys.executable,'-m','deploy_pack.cli','--version'])]:
        p,d=run(cmd,env=env); elapsed+=d
        checks.append({'name':label,'status':'PASS' if p.returncode==0 else 'FAIL','output':p.stdout.strip()})
        if p.returncode: failures.append({'check':label,'output':p.stdout[-8000:]})
    complete_modules=sum(len(b['modules']) for b in batches if b['status']=='PASS')
    status='PASS' if not failures and complete_modules==len(TESTS) else 'FAIL'
    result={'gate':'DEPLOY-PACK-FREEZE-01','version':'1.9.1','status':status,'testFiles':len(TESTS),'executedTestFiles':complete_modules,'tests':total,'elapsedSeconds':round(elapsed,3),'batches':batches,'checks':checks,'failures':failures}
    out=ROOT/'docs/manifests/DEPLOY-PACK-FREEZE-01-TEST-EVIDENCE.json'; out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(f"DEPLOY-PACK-FREEZE-01: {status}"); print(f"  test files: {complete_modules}/{len(TESTS)}"); print(f"  tests     : {total}"); print(f"  evidence  : {out.relative_to(ROOT)}")
    if failures: print(json.dumps(failures,indent=2),file=sys.stderr)
    return 0 if status=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
