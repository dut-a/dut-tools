#!/usr/bin/env python3
from __future__ import annotations
import argparse, dataclasses, datetime as dt, hashlib, json, os, re, shutil, subprocess, sys, tomllib, uuid
from pathlib import Path

VERSION="1.3.1"
SUCCESS=0
VALIDATION_FAILED_ROLLED_BACK=1
INVALID_INPUT=2
DIRTY_REPOSITORY=3
APPLY_FAILED_ROLLED_BACK=4
ROLLBACK_FAILED=5
UNSUPPORTED_REPOSITORY=6

class RepoPatchError(RuntimeError): pass
class InvalidInput(RepoPatchError): pass
class UnsupportedRepository(RepoPatchError): pass

@dataclasses.dataclass
class Snapshot:
    path: Path
    existed: bool
    data: bytes|None
    mode: int|None

@dataclasses.dataclass
class Validation:
    name: str
    command: list[str]
    cwd: Path
    returncode: int|None=None
    stdout: str=""
    stderr: str=""

def run(cmd,cwd,timeout=180):
    return subprocess.run(cmd,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)

def git_root(start:Path)->Path:
    cp=run(["git","rev-parse","--show-toplevel"],start)
    if cp.returncode: raise UnsupportedRepository("not inside a Git repository")
    return Path(cp.stdout.strip()).resolve()

def canonical(path:Path)->Path:
    return path.expanduser().resolve()

def relative_path(path:Path,root:Path)->Path:
    return canonical(path).relative_to(canonical(root))

def same_path(a:Path,b:Path)->bool:
    return canonical(a)==canonical(b)

def safe_path(root:Path,rel:str)->Path:
    root_canonical=canonical(root)
    p=canonical(root_canonical/rel)
    try:
        p.relative_to(root_canonical)
    except ValueError as e:
        raise InvalidInput(f"path escapes repository: {rel}") from e
    return p

def dirty(root:Path)->bool:
    cp=run(["git","status","--porcelain"],root)
    if cp.returncode: raise UnsupportedRepository("git status failed")
    return bool(cp.stdout.strip())

def parse_patch_paths(patch:Path)->set[str]:
    if not patch.is_file(): raise InvalidInput(f"patch not found: {patch}")
    out=set()
    for line in patch.read_text(encoding="utf-8",errors="replace").splitlines():
        if line.startswith(("+++ ","--- ")):
            raw=line[4:].strip()
            if raw=="/dev/null": continue
            if raw.startswith(("a/","b/")): raw=raw[2:]
            if raw: out.add(raw)
    if not out: raise InvalidInput("patch contains no recognizable file paths")
    return out

def take_snapshots(root,rels):
    out={}
    for rel in sorted(set(rels)):
        p=safe_path(root,rel)
        if p.is_file():
            out[rel]=Snapshot(p,True,p.read_bytes(),p.stat().st_mode)
        else:
            out[rel]=Snapshot(p,False,None,None)
    return out

def restore(snaps):
    errors=[]
    for rel,s in snaps.items():
        try:
            if s.existed:
                s.path.parent.mkdir(parents=True,exist_ok=True)
                s.path.write_bytes(s.data or b"")
                if s.mode is not None: os.chmod(s.path,s.mode)
            elif s.path.exists():
                if s.path.is_dir(): shutil.rmtree(s.path)
                else: s.path.unlink()
        except Exception as e:
            errors.append(f"{rel}: {e}")
    if errors: raise RepoPatchError("; ".join(errors))

def detect_adapter(root):
    if (root/"pom.xml").exists():
        return "maven-spring"
    if any((root/name).exists() for name in ("settings.gradle","settings.gradle.kts","build.gradle","build.gradle.kts")):
        return "gradle"
    if (root/"composer.json").exists():
        return "composer-php"
    return "generic"

def modules(root):
    return sorted({p.parent.resolve() for p in root.rglob("pom.xml")})

def containing_module(path,mods,root):
    candidates=[]
    for m in mods:
        try: path.resolve().relative_to(m); candidates.append(m)
        except ValueError: pass
    return max(candidates,key=lambda p:len(p.parts)) if candidates else root

def affected_selectors(root,paths):
    mods=modules(root)
    sels=set()
    for p in paths:
        m=containing_module(p,mods,root)
        sels.add("." if same_path(m,root) else relative_path(m,root).as_posix())
    return sorted(sels)

def has_spotless(root):
    for pom in root.rglob("pom.xml"):
        try:
            if "spotless-maven-plugin" in pom.read_text(encoding="utf-8"): return True
        except OSError: pass
    return False

def spring_modules(root):
    out=[]
    for pom in root.rglob("pom.xml"):
        try:
            if "spring-boot" in pom.read_text(encoding="utf-8"): out.append(pom.parent.resolve())
        except OSError: pass
    return out

def maven_command(root):
    if shutil.which("mvn"): return ["mvn"]
    if (root/"mvnw").exists(): return [str(root/"mvnw")]
    raise UnsupportedRepository("neither mvn nor mvnw is available")


def gradle_settings_file(root):
    for name in ("settings.gradle.kts","settings.gradle"):
        p=root/name
        if p.exists():
            return p
    return None

def gradle_projects(root):
    projects={":":root}
    sf=gradle_settings_file(root)
    if sf:
        try:
            content=sf.read_text(encoding="utf-8")
        except OSError:
            content=""
        refs=set(re.findall(r"""['"](:[^'"]+)['"]""",content))
        for ref in refs:
            projects[ref]=(root/ref.lstrip(":").replace(":","/")).resolve()
    for candidate in list(root.rglob("build.gradle"))+list(root.rglob("build.gradle.kts")):
        mod=candidate.parent.resolve()
        rel=relative_path(mod,root).as_posix()
        ref=":" if rel=="." else ":"+rel.replace("/",":")
        projects.setdefault(ref,mod)
    return projects

def containing_gradle_project(path,projects,root):
    matches=[]
    for ref,project in projects.items():
        try:
            path.resolve().relative_to(project)
            matches.append((project,ref))
        except ValueError:
            pass
    if not matches:
        return ":",root
    project,ref=max(matches,key=lambda item:len(item[0].parts))
    return ref,project

def gradle_command(root):
    wrapper=root/"gradlew"
    if wrapper.exists():
        return [str(wrapper)]
    if shutil.which("gradle"):
        return ["gradle"]
    raise UnsupportedRepository("neither gradlew nor gradle is available")

def gradle_has_spotless(root):
    for p in list(root.rglob("build.gradle"))+list(root.rglob("build.gradle.kts")):
        try:
            if "spotless" in p.read_text(encoding="utf-8").lower():
                return True
        except OSError:
            pass
    return False

def gradle_spring_boot_projects(root):
    out=set()
    for ref,project in gradle_projects(root).items():
        for name in ("build.gradle","build.gradle.kts"):
            build=project/name
            if not build.exists():
                continue
            try:
                body=build.read_text(encoding="utf-8").lower()
            except OSError:
                continue
            if "org.springframework.boot" in body or "spring-boot" in body:
                out.add(ref)
    return out

def gradle_task(ref,task):
    return task if ref==":" else f"{ref}:{task}"

def gradle_validations(root,paths,startup_probe):
    projects=gradle_projects(root)
    affected=sorted({containing_gradle_project(path,projects,root)[0] for path in paths})
    base=gradle_command(root)
    tasks=[]
    for ref in affected:
        tasks += [gradle_task(ref,"classes"),gradle_task(ref,"testClasses")]
    vals=[Validation("gradle-classes",base+["--no-daemon",*tasks],root)]
    if gradle_has_spotless(root):
        vals.append(Validation("gradle-spotless-check",base+["--no-daemon","spotlessCheck"],root))
    if startup_probe:
        boot=gradle_spring_boot_projects(root)
        smoke=[gradle_task(ref,"test") for ref in affected if ref in boot]
        if smoke:
            vals.append(Validation("gradle-spring-context-smoke",
                                   base+["--no-daemon",*smoke,"--tests","*Context*Test"],root))
    return vals


def composer_packages(root):
    packages = {root.resolve()}
    for manifest in root.rglob("composer.json"):
        if any(part in {"vendor", "node_modules", ".git", "build", "dist"} for part in manifest.parts):
            continue
        packages.add(manifest.parent.resolve())
    return sorted(packages, key=lambda p: (len(p.parts), str(p)))

def containing_composer_package(path, packages, root):
    candidates = []
    for package in packages:
        try:
            path.resolve().relative_to(package)
            candidates.append(package)
        except ValueError:
            pass
    return max(candidates, key=lambda p: len(p.parts)) if candidates else root

def composer_command(package, root):
    for candidate in (package/"composer.phar", root/"composer.phar"):
        if candidate.is_file():
            php = shutil.which("php")
            if not php:
                raise UnsupportedRepository("php is required to run composer.phar")
            return [php, str(candidate)]
    if shutil.which("composer"):
        return ["composer"]
    raise UnsupportedRepository("neither repository-local composer.phar nor composer is available")

def php_command():
    php = shutil.which("php")
    if not php:
        raise UnsupportedRepository("php is not available")
    return php

def package_phpunit(package):
    candidates = [
        package/"vendor/bin/phpunit",
        package/"vendor/bin/phpunit.bat",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None

def composer_php_validations(root, paths, startup_probe):
    del startup_probe  # PHP adapter has no framework-wide startup assumption.
    packages = composer_packages(root)
    affected = sorted({
        containing_composer_package(path, packages, root)
        for path in paths
    }, key=str)

    vals = []
    php = php_command()

    php_files = sorted({p for p in paths if p.suffix.lower() == ".php"}, key=str)
    for path in php_files:
        vals.append(Validation(
            f"php-lint:{relative_path(path,root).as_posix()}",
            [php, "-l", str(path)],
            root
        ))

    for package in affected:
        composer = composer_command(package, root)
        vals.append(Validation(
            f"composer-validate:{relative_path(package,root).as_posix() or '.'}",
            composer + ["validate", "--no-check-publish", "--no-interaction"],
            package
        ))

        phpunit = package_phpunit(package)
        if phpunit:
            vals.append(Validation(
                f"phpunit:{relative_path(package,root).as_posix() or '.'}",
                [phpunit, "--colors=never"],
                package
            ))
        elif (package/"artisan").is_file():
            vals.append(Validation(
                f"laravel-test:{relative_path(package,root).as_posix() or '.'}",
                [php, "artisan", "test", "--no-interaction"],
                package
            ))
    return vals

def load_repo_patch_config(root, explicit=None):
    path = Path(explicit).expanduser().resolve() if explicit else root/".repo-patch.toml"
    if not path.exists(): return {}, path
    try:
        with path.open("rb") as fh: data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise InvalidInput(f"invalid repo-patch config {path}: {e}") from e
    if data.get("version",1) != 1: raise InvalidInput("repo-patch config version must be 1")
    return data,path

def generic_config_validations(root, config):
    generic=config.get("generic",{}) or {}
    if not isinstance(generic,dict): raise InvalidInput("[generic] must be a table")
    entries=generic.get("validations",[]) or []
    if not isinstance(entries,list): raise InvalidInput("generic.validations must be an array of tables")
    vals=[]
    for idx,item in enumerate(entries):
        if not isinstance(item,dict): raise InvalidInput(f"generic.validations[{idx}] must be a table")
        name=str(item.get("name","")).strip(); argv=item.get("argv"); cwd_raw=item.get("cwd",".")
        if not name: raise InvalidInput(f"generic.validations[{idx}] requires name")
        if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv):
            raise InvalidInput(f"generic.validations[{idx}].argv must be a non-empty string array")
        cwd=safe_path(root,str(cwd_raw))
        if not cwd.exists() or not cwd.is_dir(): raise InvalidInput(f"generic validation cwd is not a directory: {cwd_raw}")
        vals.append(Validation(f"generic:{name}",list(argv),cwd))
    return vals

def generic_file_validation_command(root,path):
    return [sys.executable,str(Path(__file__).resolve()),"_validate-file","--root",str(root),"--path",str(path)]

def generic_validations(root,paths,config):
    vals=[Validation(f"generic-file:{relative_path(p,root).as_posix()}",generic_file_validation_command(root,p),root) for p in sorted(set(paths),key=str)]
    vals.extend(generic_config_validations(root,config))
    return vals

def validate_generic_file(root,path):
    root=root.resolve(); path=path.resolve()
    try: relative_path(path,root)
    except ValueError as e: raise InvalidInput("validation path escapes repository") from e
    if not path.exists(): return
    if not path.is_file(): raise InvalidInput(f"touched path is not a regular file: {path}")
    try: data=path.read_bytes()
    except OSError as e: raise InvalidInput(f"cannot read touched file {path}: {e}") from e
    if b"\\x00" in data: return
    try: text=data.decode("utf-8")
    except UnicodeDecodeError: return
    for line in text.splitlines():
        if line.startswith(("<<<<<<< ","=======",">>>>>>> ")): raise InvalidInput(f"unresolved merge conflict marker in {path}")
    suffix=path.suffix.lower()
    if suffix==".json":
        try: json.loads(text)
        except json.JSONDecodeError as e: raise InvalidInput(f"invalid JSON {path}: {e}") from e
    elif suffix==".toml":
        try: tomllib.loads(text)
        except tomllib.TOMLDecodeError as e: raise InvalidInput(f"invalid TOML {path}: {e}") from e
    elif suffix==".py":
        try: compile(text,str(path),"exec")
        except SyntaxError as e: raise InvalidInput(f"invalid Python {path}:{e.lineno}: {e.msg}") from e
    elif suffix in {".sh",".bash"} or (text.startswith("#!") and "bash" in text.splitlines()[0]):
        bash=shutil.which("bash")
        if bash:
            cp=run([bash,"-n",str(path)],root)
            if cp.returncode: raise InvalidInput(cp.stderr.strip() or f"shell syntax failure: {path}")

def cmd_validate_file(args):
    try:
        validate_generic_file(Path(args.root),Path(args.path)); return SUCCESS
    except InvalidInput as e:
        print(f"ERROR: {e}",file=sys.stderr); return INVALID_INPUT

def validations(root,paths,startup_probe,adapter,config=None):
    if adapter=="gradle": return gradle_validations(root,paths,startup_probe)
    if adapter=="composer-php": return composer_php_validations(root,paths,startup_probe)
    if adapter=="generic": return generic_validations(root,paths,config or {})
    base=maven_command(root)
    sels=affected_selectors(root,paths)
    pl=",".join(sels)
    vals=[Validation("test-compile",base+["-q","-pl",pl,"-am","test-compile"],root)]
    if has_spotless(root):
        vals.append(Validation("spotless-check",base+["-q","-pl",pl,"-am","spotless:check"],root))
    if startup_probe:
        mods=modules(root)
        touched={containing_module(p,mods,root) for p in paths}
        boot=[m for m in spring_modules(root) if m in touched]
        if boot:
            boot_sel=",".join("." if same_path(m,root) else relative_path(m,root).as_posix() for m in boot)
            vals.append(Validation(
                "spring-context-smoke",
                base+["-q","-pl",boot_sel,"-am","test","-Dtest=*Context*Test","-DfailIfNoTests=false"],
                root
            ))
    return vals

def execute(vals):
    for v in vals:
        cp=run(v.command,v.cwd,timeout=300)
        v.returncode,v.stdout,v.stderr=cp.returncode,cp.stdout,cp.stderr
        if cp.returncode: return False
    return True

def apply_patch(root,patch):
    cp=run(["git","apply","--whitespace=nowarn",str(patch)],root)
    if cp.returncode: raise RepoPatchError(cp.stderr.strip() or "git apply failed")

def replacements(root,specs):
    out=[]
    for spec in specs:
        if "=" not in spec: raise InvalidInput(f"invalid --replace: {spec}")
        left,right=spec.split("=",1)
        dst=safe_path(root,left)
        src=Path(right).expanduser().resolve()
        if not src.is_file(): raise InvalidInput(f"replacement source missing: {src}")
        out.append((dst,src))
    return out

def digest(path):
    if not path.is_file(): return None
    return hashlib.sha256(path.read_bytes()).hexdigest()

def manifest(root,run_id,result,adapter,paths,snaps,vals,error=None):
    d=root/".repo-patch"/"runs"/run_id
    d.mkdir(parents=True,exist_ok=True)
    doc={
        "tool":"repo-patch","version":VERSION,"run_id":run_id,"result":result,"adapter":adapter,
        "timestamp_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "mandatory_rollback":True,
        "files":[{
            "path":relative_path(p,root).as_posix(),
            "before_sha256":hashlib.sha256(snaps[relative_path(p,root).as_posix()].data).hexdigest()
                if snaps[relative_path(p,root).as_posix()].data is not None else None,
            "after_sha256":digest(p),
        } for p in paths],
        "validations":[{
            "name":v.name,"command":v.command,"returncode":v.returncode,
            "stdout":v.stdout[-4000:],"stderr":v.stderr[-4000:]
        } for v in vals],
        "error":error
    }
    out=d/"manifest.json"
    out.write_text(json.dumps(doc,indent=2)+"\n",encoding="utf-8")
    return out

def build_plan(args):
    root=git_root(Path(args.root).expanduser().resolve())
    adapter=detect_adapter(root) if args.adapter=="auto" else args.adapter
    if adapter not in {"maven-spring","gradle","composer-php","generic"}:
        raise UnsupportedRepository(f"unsupported adapter: {adapter}")
    rels=set()
    if args.patch: rels |= parse_patch_paths(Path(args.patch).expanduser().resolve())
    repl=replacements(root,args.replace)
    rels |= {relative_path(d,root).as_posix() for d,_ in repl}
    if not rels: raise InvalidInput("provide --patch and/or --replace")
    paths=[safe_path(root,r) for r in sorted(rels)]
    config,_=load_repo_patch_config(root,args.config)
    vals=validations(root,paths,args.startup_probe,adapter,config)
    return root,adapter,sorted(rels),paths,repl,vals

def show_plan(root,adapter,rels,vals):
    print(f"root: {root}\nadapter: {adapter}\nfiles:")
    for r in rels: print(f"  - {r}")
    print("validations:")
    for v in vals: print("  - "+" ".join(v.command))
    print("rollback: mandatory on any validation failure")

def cmd_plan(args):
    root,adapter,rels,paths,repl,vals=build_plan(args); show_plan(root,adapter,rels,vals); return 0

def cmd_check(args):
    root,adapter,rels,paths,repl,vals=build_plan(args); show_plan(root,adapter,rels,vals); print("check: PASS"); return 0

def cmd_doctor(args):
    try:
        root=git_root(Path(args.root).expanduser().resolve())
        print(f"PASS git repository: {root}")
        adapter=detect_adapter(root)
        print(f"PASS adapter: {adapter}")
        if adapter=="maven-spring":
            maven_command(root); print("PASS Maven command available")
        elif adapter=="gradle":
            gradle_command(root); print("PASS Gradle command available")
        elif adapter=="composer-php":
            php_command(); print("PASS PHP command available")
            composer_command(root,root); print("PASS Composer command available")
        else:
            config,config_path=load_repo_patch_config(root,None)
            print("PASS generic adapter")
            print(f"PASS generic config: {config_path if config_path.exists() else 'not present (built-in checks only)'}")
        print("PASS mandatory rollback engine available")
        return 0
    except RepoPatchError as e:
        print(f"FAIL {e}"); return UNSUPPORTED_REPOSITORY

def cmd_apply(args):
    try:
        root,adapter,rels,paths,repl,vals=build_plan(args)
        if dirty(root) and not args.allow_dirty:
            print("ERROR: repository is dirty; use --allow-dirty deliberately",file=sys.stderr)
            return DIRTY_REPOSITORY
        snaps=take_snapshots(root,rels)
        run_id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
        try:
            if args.patch: apply_patch(root,Path(args.patch).expanduser().resolve())
            for dst,src in repl:
                dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
            if not execute(vals):
                try: restore(snaps)
                except Exception as rb:
                    manifest(root,run_id,"ROLLBACK_FAILED",adapter,paths,snaps,vals,str(rb))
                    print(f"ERROR: validation failed and rollback failed: {rb}",file=sys.stderr)
                    return ROLLBACK_FAILED
                mf=manifest(root,run_id,"ROLLED_BACK",adapter,paths,snaps,vals,"validation failed")
                print(f"ERROR: validation failed; changes rolled back\nmanifest: {mf}",file=sys.stderr)
                return VALIDATION_FAILED_ROLLED_BACK
            mf=manifest(root,run_id,"APPLIED",adapter,paths,snaps,vals)
            print(f"APPLIED {len(paths)} file(s)\nmanifest: {mf}"); return SUCCESS
        except Exception as e:
            try: restore(snaps)
            except Exception as rb:
                manifest(root,run_id,"ROLLBACK_FAILED",adapter,paths,snaps,vals,f"{e}; rollback: {rb}")
                return ROLLBACK_FAILED
            mf=manifest(root,run_id,"ROLLED_BACK",adapter,paths,snaps,vals,str(e))
            print(f"ERROR: apply failed; changes rolled back: {e}\nmanifest: {mf}",file=sys.stderr)
            return APPLY_FAILED_ROLLED_BACK
    except InvalidInput as e:
        print(f"ERROR: {e}",file=sys.stderr); return INVALID_INPUT
    except UnsupportedRepository as e:
        print(f"ERROR: {e}",file=sys.stderr); return UNSUPPORTED_REPOSITORY

def add_common(p):
    p.add_argument("--root",default=".")
    p.add_argument("--adapter",choices=["auto","maven-spring","gradle","composer-php","generic"],default="auto")
    p.add_argument("--config",help="repo-patch TOML config (default: <root>/.repo-patch.toml)")
    p.add_argument("--patch")
    p.add_argument("--replace",action="append",default=[])
    p.add_argument("--startup-probe",dest="startup_probe",action=argparse.BooleanOptionalAction,default=True)

def parser():
    p=argparse.ArgumentParser(prog="repo-patch",
        description="Plan-first repository patching with mandatory rollback on validation failure.")
    p.add_argument("--version",action="version",version=f"repo-patch {VERSION}")
    sp=p.add_subparsers(dest="command",required=True)
    q=sp.add_parser("plan"); add_common(q); q.set_defaults(func=cmd_plan)
    q=sp.add_parser("check"); add_common(q); q.set_defaults(func=cmd_check)
    q=sp.add_parser("apply"); add_common(q); q.add_argument("--allow-dirty",action="store_true"); q.set_defaults(func=cmd_apply)
    q=sp.add_parser("doctor"); q.add_argument("--root",default="."); q.set_defaults(func=cmd_doctor)
    q=sp.add_parser("_validate-file",help=argparse.SUPPRESS)
    q.add_argument("--root",required=True); q.add_argument("--path",required=True); q.set_defaults(func=cmd_validate_file)
    return p

if __name__=="__main__":
    a=parser().parse_args()
    raise SystemExit(a.func(a))
