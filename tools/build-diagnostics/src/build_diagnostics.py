#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VERSION="1.0.0"

BUILD_SUCCEEDED=0
BUILD_FAILED=1
INVALID_CONFIG_OR_ARGUMENTS=2
EXECUTION_ERROR=3

class DiagnosticsError(RuntimeError): pass
class ConfigError(DiagnosticsError): pass

@dataclasses.dataclass(frozen=True)
class Classifier:
    name:str
    severity:str
    regex:re.Pattern[str]

@dataclasses.dataclass(frozen=True)
class Forbidden:
    name:str
    regex:re.Pattern[str]

@dataclasses.dataclass(frozen=True)
class Diagnostic:
    severity:str
    category:str
    line:str
    line_number:int

@dataclasses.dataclass
class Policy:
    max_warnings:int|None=None
    max_errors:int|None=None
    category_budget:dict[str,int]=dataclasses.field(default_factory=dict)
    forbidden:list[Forbidden]=dataclasses.field(default_factory=list)

BUILTINS=[
    Classifier("error","error",re.compile(r"(?i)(?:^|\b)(?:\[?ERROR\]?|FAILURE|FATAL)(?:\b|:)")),
    Classifier("warning","warning",re.compile(r"(?i)(?:^|\b)(?:\[?WARN(?:ING)?\]?)(?:\b|:)")),
    Classifier("note","note",re.compile(r"(?i)(?:^|\b)(?:NOTE|INFO)(?:\b|:)")),
]

def compile_regex(value:str,where:str)->re.Pattern[str]:
    try: return re.compile(value)
    except re.error as e: raise ConfigError(f"{where}: invalid regex: {e}") from e

def load_config(path:Path|None)->tuple[list[Classifier],Policy]:
    if path is None or not path.exists():
        return [],Policy()
    try:
        with path.open("rb") as fh: raw=tomllib.load(fh)
    except (OSError,tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    if raw.get("version",1)!=1:
        raise ConfigError("config version must be 1")

    custom=[]
    for i,item in enumerate(raw.get("classifier",[]) or []):
        if not isinstance(item,dict): raise ConfigError(f"classifier[{i}] must be a table")
        name=str(item.get("name","")).strip()
        severity=str(item.get("severity","")).strip()
        regex=str(item.get("regex",""))
        if not name or severity not in {"error","warning","note"} or not regex:
            raise ConfigError(f"classifier[{i}] requires name, severity error|warning|note, regex")
        custom.append(Classifier(name,severity,compile_regex(regex,f"classifier[{i}]")))

    budget_raw=raw.get("budget",{}) or {}
    if not isinstance(budget_raw,dict): raise ConfigError("[budget] must be a table")
    def budget_int(name):
        value=budget_raw.get(name)
        if value is None: return None
        if not isinstance(value,int) or value<0: raise ConfigError(f"budget.{name} must be >= 0")
        return value
    cat=budget_raw.get("category",{}) or {}
    if not isinstance(cat,dict): raise ConfigError("[budget.category] must be a table")
    category_budget={}
    for k,v in cat.items():
        if not isinstance(v,int) or v<0: raise ConfigError(f"budget.category.{k} must be >= 0")
        category_budget[str(k)]=v

    forbidden=[]
    for i,item in enumerate(raw.get("forbidden",[]) or []):
        if not isinstance(item,dict): raise ConfigError(f"forbidden[{i}] must be a table")
        name=str(item.get("name","")).strip()
        regex=str(item.get("regex",""))
        if not name or not regex: raise ConfigError(f"forbidden[{i}] requires name and regex")
        forbidden.append(Forbidden(name,compile_regex(regex,f"forbidden[{i}]")))

    return custom,Policy(
        max_warnings=budget_int("max_warnings"),
        max_errors=budget_int("max_errors"),
        category_budget=category_budget,
        forbidden=forbidden,
    )

def classify(lines:list[str],custom:list[Classifier])->list[Diagnostic]:
    classifiers=custom+BUILTINS
    out=[]
    for idx,line in enumerate(lines,1):
        for classifier in classifiers:
            if classifier.regex.search(line):
                out.append(Diagnostic(classifier.severity,classifier.name,line,idx))
                break
    return out

def policy_violations(diags:list[Diagnostic],policy:Policy,lines:list[str])->list[str]:
    violations=[]
    severities=Counter(d.severity for d in diags)
    categories=Counter(d.category for d in diags)
    if policy.max_warnings is not None and severities["warning"]>policy.max_warnings:
        violations.append(f"warnings {severities['warning']} > budget {policy.max_warnings}")
    if policy.max_errors is not None and severities["error"]>policy.max_errors:
        violations.append(f"errors {severities['error']} > budget {policy.max_errors}")
    for category,max_count in sorted(policy.category_budget.items()):
        if categories[category]>max_count:
            violations.append(f"category {category} {categories[category]} > budget {max_count}")
    for forbidden in policy.forbidden:
        count=sum(1 for line in lines if forbidden.regex.search(line))
        if count:
            violations.append(f"forbidden {forbidden.name}: {count} match(es)")
    return violations

def load_baseline(path:Path|None)->dict[str,Any]|None:
    if path is None: return None
    if not path.is_file(): raise ConfigError(f"baseline not found: {path}")
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as e: raise ConfigError(f"invalid baseline {path}: {e}") from e
    if not isinstance(data,dict): raise ConfigError("baseline must be a JSON object")
    return data

def summary(diags:list[Diagnostic])->dict[str,Any]:
    sev=Counter(d.severity for d in diags)
    cat=Counter(d.category for d in diags)
    return {
        "errors":sev["error"],
        "warnings":sev["warning"],
        "notes":sev["note"],
        "categories":dict(sorted(cat.items())),
    }

def baseline_delta(current:dict[str,Any],baseline:dict[str,Any]|None)->dict[str,Any]|None:
    if not baseline: return None
    old=(baseline.get("summary") or {}).get("diagnostics") or baseline.get("diagnostics") or {}
    old_categories=old.get("categories") or {}
    allcats=set(current["categories"])|set(old_categories)
    return {
        "errors":current["errors"]-int(old.get("errors",0)),
        "warnings":current["warnings"]-int(old.get("warnings",0)),
        "notes":current["notes"]-int(old.get("notes",0)),
        "categories":{k:current["categories"].get(k,0)-int(old_categories.get(k,0)) for k in sorted(allcats)}
    }

def parser():
    p=argparse.ArgumentParser(
        prog="build-diagnostics",
        description="Capture build output, classify diagnostics, and enforce project-owned warning/error policy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  build-diagnostics -- mvn clean verify
  build-diagnostics --format json -- ./gradlew build
  build-diagnostics --check -- composer test
  build-diagnostics --baseline previous.json -- mvn verify
""")
    p.add_argument("--version",action="version",version=f"build-diagnostics {VERSION}")
    p.add_argument("--config")
    p.add_argument("--format",choices=["text","json"],default="text")
    p.add_argument("--report")
    p.add_argument("--baseline")
    p.add_argument("--check",action="store_true")
    p.add_argument("--cwd",default=".")
    p.add_argument("--sample-limit",type=int,default=5)
    p.add_argument("command",nargs=argparse.REMAINDER)
    return p

def main():
    args=parser().parse_args()
    try:
        command=list(args.command)
        if command and command[0]=="--": command=command[1:]
        if not command: raise ConfigError("build command is required after --")
        if args.sample_limit<0: raise ConfigError("--sample-limit must be >= 0")
        cwd=Path(args.cwd).expanduser().resolve()
        if not cwd.is_dir(): raise ConfigError(f"cwd is not a directory: {cwd}")
        config=Path(args.config).expanduser().resolve() if args.config else cwd/".build-diagnostics.toml"
        custom,policy=load_config(config if config.exists() else None)
        baseline=load_baseline(Path(args.baseline).expanduser().resolve() if args.baseline else None)

        start=time.perf_counter()
        try:
            cp=subprocess.run(command,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        except FileNotFoundError as e:
            print(f"ERROR: command not found: {e.filename}",file=sys.stderr); return EXECUTION_ERROR
        duration_ms=int((time.perf_counter()-start)*1000)

        combined=[]
        if cp.stdout: combined.extend(cp.stdout.splitlines())
        if cp.stderr: combined.extend(cp.stderr.splitlines())
        diags=classify(combined,custom)
        diag_summary=summary(diags)
        violations=policy_violations(diags,policy,combined)
        delta=baseline_delta(diag_summary,baseline)

        samples=defaultdict(list)
        for d in diags:
            if len(samples[d.category])<args.sample_limit:
                samples[d.category].append({"severity":d.severity,"line_number":d.line_number,"line":d.line})

        payload={
            "tool":"build-diagnostics",
            "version":VERSION,
            "command":command,
            "cwd":str(cwd),
            "exit_code":cp.returncode,
            "duration_ms":duration_ms,
            "summary":{
                "build_succeeded":cp.returncode==0,
                "diagnostics":diag_summary,
                "policy_violations":len(violations),
            },
            "policy_violations":violations,
            "baseline_delta":delta,
            "samples":dict(sorted(samples.items())),
            "stdout":cp.stdout[-12000:],
            "stderr":cp.stderr[-12000:],
        }

        if args.format=="json":
            rendered=json.dumps(payload,indent=2)+"\n"
        else:
            lines=[
                f"BUILD {'PASS' if cp.returncode==0 else 'FAIL'} exit={cp.returncode} duration_ms={duration_ms}",
                f"DIAGNOSTICS errors={diag_summary['errors']} warnings={diag_summary['warnings']} notes={diag_summary['notes']}",
            ]
            for category,count in diag_summary["categories"].items():
                lines.append(f"  {category}: {count}")
            if delta:
                lines.append(f"DELTA errors={delta['errors']:+d} warnings={delta['warnings']:+d} notes={delta['notes']:+d}")
            for violation in violations:
                lines.append(f"POLICY FAIL {violation}")
            rendered="\n".join(lines)+"\n"

        sys.stdout.write(rendered)
        if args.report:
            rp=Path(args.report).expanduser().resolve()
            rp.parent.mkdir(parents=True,exist_ok=True)
            if rp.suffix.lower()==".json":
                rp.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
            else:
                rp.write_text(rendered,encoding="utf-8")

        failed=cp.returncode!=0 or (args.check and bool(violations))
        return BUILD_FAILED if failed else BUILD_SUCCEEDED
    except ConfigError as e:
        print(f"ERROR: {e}",file=sys.stderr); return INVALID_CONFIG_OR_ARGUMENTS
    except Exception as e:
        print(f"ERROR: execution failure: {e}",file=sys.stderr); return EXECUTION_ERROR

if __name__=="__main__":
    raise SystemExit(main())
