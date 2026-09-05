#!/usr/bin/env python3
from __future__ import annotations
import argparse, dataclasses, fnmatch, hashlib, json, os, stat, subprocess, sys, tarfile, tempfile, time, tomllib, zipfile
from pathlib import Path
from typing import Any

VERSION="1.0.0"
DEFAULT_CONFIG=".release-archive.toml"
DEFAULT_EXCLUDES=[".git/**","**/.git/**","node_modules/**","vendor/**","target/**","build/**","dist/**","out/**",".gradle/**","**/__pycache__/**","**/*.pyc",".DS_Store",".env",".env.*","**/.env","**/.env.*","*.log","*.tmp"]

class Error(RuntimeError): pass

@dataclasses.dataclass
class Settings:
    root: Path
    format: str="zip"
    require_clean: bool=True
    include_untracked: bool=False
    include: list[str]=dataclasses.field(default_factory=list)
    exclude: list[str]=dataclasses.field(default_factory=list)
    output: str|None=None
    source_date_epoch: int=0

def run(cmd,cwd):
    try:
        return subprocess.run(cmd,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd,127,"","not found")

def git_root(path):
    cp=run(["git","rev-parse","--show-toplevel"],path)
    return Path(cp.stdout.strip()).resolve() if cp.returncode==0 and cp.stdout.strip() else None

def git_commit(root):
    cp=run(["git","rev-parse","HEAD"],root)
    return cp.stdout.strip() if cp.returncode==0 else None

def git_dirty(root):
    cp=run(["git","status","--porcelain"],root)
    if cp.returncode: raise Error("unable to inspect Git worktree")
    return bool(cp.stdout.strip())

def load_config(path):
    if not path or not path.exists(): return {}
    with path.open("rb") as f: raw=tomllib.load(f)
    if raw.get("version",1)!=1: raise Error("config version must be 1")
    return raw

def merge(root,args):
    cfg_path=Path(args.config).expanduser().resolve() if args.config else root/DEFAULT_CONFIG
    cfg=load_config(cfg_path if cfg_path.exists() else None)
    def pick(name,default):
        v=getattr(args,name,None)
        return cfg.get(name,default) if v is None else v
    include=list(cfg.get("include",[]))+list(args.include or [])
    exclude=list(cfg.get("exclude",[]))+list(args.exclude or [])
    for k,v in (("include",include),("exclude",exclude)):
        if not all(isinstance(x,str) for x in v): raise Error(f"{k} must contain strings")
    return Settings(root=root,format=pick("format","zip"),require_clean=bool(pick("require_clean",True)),include_untracked=bool(pick("include_untracked",False)),include=include,exclude=exclude,output=pick("output",None),source_date_epoch=int(pick("source_date_epoch",0)))

def match(path,pats): return any(fnmatch.fnmatch(path,p) for p in pats)

def git_files(root,include_untracked):
    cp=run(["git","ls-files","-z"],root)
    if cp.returncode: return None
    names=[x for x in cp.stdout.split("\0") if x]
    if include_untracked:
        cp2=run(["git","ls-files","--others","--exclude-standard","-z"],root)
        if cp2.returncode==0: names += [x for x in cp2.stdout.split("\0") if x]
    return sorted({root/x for x in names if (root/x).is_file()})

def walk_files(root):
    out=[]
    for dp,dns,fns in os.walk(root):
        dns[:]=[d for d in dns if d not in {".git","node_modules","vendor","target","build","dist","out",".gradle"}]
        for n in fns: out.append(Path(dp)/n)
    return sorted(out)

def select(settings):
    files=git_files(settings.root,settings.include_untracked)
    if files is None: files=walk_files(settings.root)
    selected=[]
    excludes=DEFAULT_EXCLUDES+settings.exclude
    for p in files:
        rel=p.relative_to(settings.root).as_posix()
        if match(rel,excludes): continue
        if settings.include and not match(rel,settings.include): continue
        selected.append(p)
    return sorted(selected,key=lambda p:p.relative_to(settings.root).as_posix())

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def manifest(settings,files,dirty):
    return {"tool":"release-archive","version":VERSION,"source_root":str(settings.root),"git_commit":git_commit(settings.root) if git_root(settings.root) else None,"dirty":dirty,"source_date_epoch":settings.source_date_epoch,"files":[{"path":p.relative_to(settings.root).as_posix(),"size":p.stat().st_size,"sha256":sha(p)} for p in files]}

def output_base(settings):
    if settings.output: return Path(settings.output).expanduser().resolve()
    return settings.root.parent/settings.root.name

def outputs(settings):
    base=output_base(settings)
    if settings.format=="zip": return [("zip",Path(str(base)+".zip"))]
    if settings.format=="tar.gz": return [("tar.gz",Path(str(base)+".tar.gz"))]
    if settings.format=="both": return [("zip",Path(str(base)+".zip")),("tar.gz",Path(str(base)+".tar.gz"))]
    raise Error("format must be zip, tar.gz, or both")

def zip_dt(epoch):
    t=time.gmtime(max(epoch,315532800))
    return (t.tm_year,t.tm_mon,t.tm_mday,t.tm_hour,t.tm_min,t.tm_sec)

def write_zip(path,settings,files,manifest_bytes):
    dtup=zip_dt(settings.source_date_epoch)
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in files:
            rel=p.relative_to(settings.root).as_posix()
            info=zipfile.ZipInfo(rel,date_time=dtup)
            mode=p.stat().st_mode
            info.external_attr=((stat.S_IMODE(mode) & 0o777)<<16)
            info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,p.read_bytes())
        info=zipfile.ZipInfo("RELEASE-MANIFEST.json",date_time=dtup); info.external_attr=(0o644<<16); info.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(info,manifest_bytes)

def write_targz(path,settings,files,manifest_bytes):
    # gzip header mtime is normalized by constructing through gzip fileobj.
    import gzip, io
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=settings.source_date_epoch,compresslevel=9) as gz:
            with tarfile.open(fileobj=gz,mode="w") as t:
                for p in files:
                    rel=p.relative_to(settings.root).as_posix()
                    info=t.gettarinfo(str(p),arcname=rel)
                    info.mtime=settings.source_date_epoch; info.uid=0; info.gid=0; info.uname=""; info.gname=""
                    with p.open("rb") as f: t.addfile(info,f)
                info=tarfile.TarInfo("RELEASE-MANIFEST.json"); info.size=len(manifest_bytes); info.mtime=settings.source_date_epoch; info.mode=0o644; info.uid=0; info.gid=0
                t.addfile(info,io.BytesIO(manifest_bytes))

def write_sidecar(path):
    digest=sha(path)
    side=Path(str(path)+".sha256")
    side.write_text(f"{digest}  {path.name}\n",encoding="utf-8")

def build(settings,force=False,check=False,dry=False):
    gr=git_root(settings.root); dirty=False
    if gr:
        dirty=git_dirty(gr)
        if settings.require_clean and dirty: raise Error("Git worktree is dirty; commit/stash changes or use --allow-dirty")
    files=select(settings)
    if not files: raise Error("no files selected")
    outs=outputs(settings)
    for _,p in outs:
        if p.exists() and not force and not check and not dry: raise Error(f"output exists: {p}; use --force")
    print(f"selected: {len(files)}")
    for kind,p in outs: print(f"{kind}: {p}")
    if dry:
        for p in files: print("  +",p.relative_to(settings.root).as_posix())
        return 0
    if check: return 0
    mbytes=(json.dumps(manifest(settings,files,dirty),sort_keys=True,indent=2)+"\n").encode()
    for kind,p in outs:
        p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists() and force: p.unlink()
        if kind=="zip": write_zip(p,settings,files,mbytes)
        else: write_targz(p,settings,files,mbytes)
        write_sidecar(p)
        print("created:",p)
    return 0

def parser():
    p=argparse.ArgumentParser(prog="release-archive",description="Build deterministic Git-aware release archives (ZIP and TAR.GZ).",formatter_class=argparse.RawDescriptionHelpFormatter,epilog="""Examples:\n  release-archive . --format zip\n  release-archive . --format tar.gz\n  release-archive . --format both\n  release-archive . --check\n  release-archive . --dry-run\n  release-archive . --include-untracked --allow-dirty\n""")
    p.add_argument("root",nargs="?",default=".")
    p.add_argument("--version",action="version",version=f"release-archive {VERSION}")
    p.add_argument("--config")
    p.add_argument("--format",choices=["zip","tar.gz","both"],default=None)
    p.add_argument("--output")
    p.add_argument("--include",action="append",default=[])
    p.add_argument("--exclude",action="append",default=[])
    p.add_argument("--include-untracked",action=argparse.BooleanOptionalAction,default=None)
    p.add_argument("--require-clean",action=argparse.BooleanOptionalAction,default=None)
    p.add_argument("--allow-dirty",action="store_true")
    p.add_argument("--source-date-epoch",type=int,default=None)
    p.add_argument("--force",action="store_true")
    p.add_argument("--dry-run",action="store_true")
    p.add_argument("--check",action="store_true")
    return p

def main():
    a=parser().parse_args()
    try:
        root=Path(a.root).expanduser().resolve()
        if not root.is_dir(): raise Error(f"root is not a directory: {root}")
        root=git_root(root) or root
        if a.allow_dirty: a.require_clean=False
        s=merge(root,a)
        return build(s,a.force,a.check,a.dry_run)
    except (Error,tomllib.TOMLDecodeError) as e:
        print(f"ERROR: {e}",file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())
