#!/usr/bin/env python3
from __future__ import annotations
import argparse, dataclasses, json, os, re, subprocess, sys, tempfile, time, tomllib
from pathlib import Path
VERSION='1.0.0'; SUCCESS=0; VERIFY_FAIL=1; INVALID=2; DIRTY=3; WRITE_FAIL=4; EXEC=5
SEMVER=re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
class ConfigError(RuntimeError): pass
@dataclasses.dataclass
class Target: name:str; type:str; path:str; pattern:str|None=None; replacement:str|None=None; count:int=1
@dataclasses.dataclass
class Verify: name:str; argv:list[str]; cwd:str='.'; timeout_seconds:int=120

def safe(root,rel):
 p=(root/rel).resolve()
 try: p.relative_to(root.resolve())
 except ValueError as e: raise ConfigError(f'path escapes repository: {rel}') from e
 return p

def load(path):
 if not path.is_file(): raise ConfigError(f'config not found: {path}')
 try:
  with path.open('rb') as fh: raw=tomllib.load(fh)
 except Exception as e: raise ConfigError(f'cannot read config: {e}') from e
 if raw.get('version')!=1: raise ConfigError('config must contain version = 1')
 settings=raw.get('settings',{}) or {}; targets=[]; verifies=[]
 for i,x in enumerate(raw.get('target',[]) or []):
  name=str(x.get('name','')).strip(); typ=str(x.get('type','')).strip(); pathv=str(x.get('path','')).strip(); count=x.get('count',1)
  if not name or typ not in {'plain','regex'} or not pathv or not isinstance(count,int) or count<1: raise ConfigError(f'invalid target[{i}]')
  pat=x.get('pattern'); repl=x.get('replacement')
  if typ=='regex':
   if not isinstance(pat,str) or not pat or not isinstance(repl,str) or '{version}' not in repl: raise ConfigError(f'invalid regex target[{i}]')
   re.compile(pat)
  targets.append(Target(name,typ,pathv,pat,repl,count))
 if not targets: raise ConfigError('at least one [[target]] is required')
 for i,x in enumerate(raw.get('verify',[]) or []):
  name=str(x.get('name','')).strip(); argv=x.get('argv'); cwd=str(x.get('cwd','.')); timeout=x.get('timeout_seconds',120)
  if not name or not isinstance(argv,list) or not argv or not all(isinstance(a,str) and a for a in argv) or not isinstance(timeout,int) or timeout<1: raise ConfigError(f'invalid verify[{i}]')
  verifies.append(Verify(name,list(argv),cwd,timeout))
 return settings,targets,verifies

def git_clean(root):
 try: cp=subprocess.run(['git','-C',str(root),'status','--porcelain'],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 except FileNotFoundError: return True
 return True if cp.returncode else not bool(cp.stdout.strip())

def derive(v,bump,suffix):
 m=SEMVER.fullmatch(v)
 if not m: raise ConfigError('--bump source must be plain SemVer x.y.z')
 a,b,c=map(int,m.groups())
 if bump=='major': a,b,c=a+1,0,0
 elif bump=='minor': b,c=b+1,0
 else: c+=1
 out=f'{a}.{b}.{c}'
 return out + (('-'+suffix.lstrip('-')) if suffix else '')

def plan(root,t,v):
 p=safe(root,t.path)
 if not p.is_file(): raise ConfigError(f'target file not found: {t.path}')
 old=p.read_text()
 if t.type=='plain': new=v+('\n' if old.endswith('\n') else '')
 else:
  new,n=re.subn(t.pattern or '',(t.replacement or '').replace('{version}',v),old,count=t.count)
  if n!=t.count: raise ConfigError(f'{t.name}: expected {t.count} replacement(s), found {n}')
 return old,new,old!=new

def atomic(p,s):
 with tempfile.NamedTemporaryFile('w',delete=False,dir=p.parent,encoding='utf-8') as fh: tmp=Path(fh.name); fh.write(s)
 os.replace(tmp,p)

def run_verify(root,v):
 cwd=safe(root,v.cwd); start=time.perf_counter()
 try:
  cp=subprocess.run(v.argv,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=v.timeout_seconds)
  return {'name':v.name,'status':'pass' if cp.returncode==0 else 'fail','exit_code':cp.returncode,'duration_ms':int((time.perf_counter()-start)*1000)}
 except Exception as e: return {'name':v.name,'status':'fail','exit_code':None,'error':str(e),'duration_ms':int((time.perf_counter()-start)*1000)}

def snapshot_changes(root,changes):
    snapshots={}
    for target,old,new,changed in changes:
        snapshots[target.path]=safe(root,target.path).read_bytes()
    return snapshots

def restore_snapshots(root,snapshots):
    for rel,data in snapshots.items():
        safe(root,rel).write_bytes(data)

def apply_changes_transactionally(root,changes):
    snapshots=snapshot_changes(root,changes)
    try:
        for target,old,new,changed in changes:
            if changed:
                atomic(safe(root,target.path),new)
    except Exception:
        restore_snapshots(root,snapshots)
        raise

def parser():
 p=argparse.ArgumentParser(prog='release-flow'); p.add_argument('--version',action='version',version=f'release-flow {VERSION}'); sp=p.add_subparsers(dest='cmd',required=True)
 for n in ('prepare','next'):
  q=sp.add_parser(n); q.add_argument('target_version'); q.add_argument('--root',default='.'); q.add_argument('--config'); q.add_argument('--write',action='store_true'); q.add_argument('--allow-dirty',action='store_true'); q.add_argument('--format',choices=['text','json'],default='text')
  if n=='next': q.add_argument('--bump',choices=['major','minor','patch']); q.add_argument('--suffix')
 q=sp.add_parser('verify'); q.add_argument('target_version'); q.add_argument('--root',default='.'); q.add_argument('--config'); q.add_argument('--format',choices=['text','json'],default='text')
 return p

def main():
 a=parser().parse_args()
 try:
  root=Path(a.root).expanduser().resolve(); cfg=Path(a.config).expanduser().resolve() if a.config else root/'.release-flow.toml'; settings,targets,verifies=load(cfg); target=a.target_version.strip()
  if a.cmd=='next' and a.bump: target=derive(target,a.bump,a.suffix)
  if a.cmd=='verify':
   rs=[]
   for t in targets:
    _,_,changed=plan(root,t,target); rs.append({'name':t.name,'status':'fail' if changed else 'pass','path':t.path})
   rs += [run_verify(root,v) for v in verifies]; failed=sum(x['status']=='fail' for x in rs)
   print(json.dumps({'target_version':target,'failed':failed,'results':rs},indent=2) if a.format=='json' else '\n'.join([f"{x['status'].upper()} {x['name']}" for x in rs]+[f'SUMMARY failed={failed}']))
   return VERIFY_FAIL if failed else SUCCESS
  if a.write and settings.get('require_clean_git',False) and not a.allow_dirty and not git_clean(root): return DIRTY
  changes=[]
  for t in targets:
   old,new,changed=plan(root,t,target); changes.append((t,old,new,changed))
  if a.format=='json': print(json.dumps({'phase':a.cmd,'target_version':target,'changes':[{'name':t.name,'path':t.path,'changed':ch} for t,_,_,ch in changes]},indent=2))
  else:
   print(f'{a.cmd.upper()} target={target}')
   for t,_,_,ch in changes: print(f"{'CHANGE' if ch else 'OK':6} {t.name}: {t.path}")
  if a.write:
   try:
    apply_changes_transactionally(root,changes)
   except Exception as e: print(f'ERROR: write failed; all targets restored: {e}',file=sys.stderr); return WRITE_FAIL
  return SUCCESS
 except ConfigError as e: print(f'ERROR: {e}',file=sys.stderr); return INVALID
 except Exception as e: print(f'ERROR: {e}',file=sys.stderr); return EXEC
if __name__=='__main__': raise SystemExit(main())
