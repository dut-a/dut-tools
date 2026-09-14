from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .core import DeployPackError, ingest_remote_evidence, load_manifest_from_archive, _validate_manifest_paths
from .lifecycle import assert_precommitted_signing_key, precommit_signing_key
from .assurance import assurance_for


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def generate_ephemeral_keypair() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    return private.private_bytes_raw(), private.public_key().public_bytes_raw()


def public_fingerprint(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()


def write_public_key_file(path: Path, public_raw: bytes) -> Path:
    path.write_text(json.dumps({
        "schemaVersion": 1,
        "algorithm": "Ed25519",
        "publicKeyBase64": base64.b64encode(public_raw).decode("ascii"),
        "publicKeySha256": public_fingerprint(public_raw),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_public_key_file(path: Path) -> bytes:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        raw = base64.b64decode(value["publicKeyBase64"], validate=True)
    except Exception as exc:
        raise DeployPackError(f"invalid verifier public-key file: {path}") from exc
    if value.get("algorithm") != "Ed25519" or len(raw) != 32:
        raise DeployPackError("invalid Ed25519 verifier public key")
    if value.get("publicKeySha256") != public_fingerprint(raw):
        raise DeployPackError("verifier public-key fingerprint mismatch")
    return raw


def verify_envelope(envelope: dict, public_raw: bytes) -> dict:
    if envelope.get("schemaVersion") != 1 or envelope.get("kind") != "deploy-pack.remote-evidence.signed":
        raise DeployPackError("not a supported signed remote evidence envelope")
    payload = envelope.get("payload")
    signature = envelope.get("signature") or {}
    signer = envelope.get("signer") or {}
    if not isinstance(payload, dict) or signature.get("algorithm") != "Ed25519":
        raise DeployPackError("signed evidence envelope is incomplete")
    if signer.get("publicKeySha256") != public_fingerprint(public_raw):
        raise DeployPackError("signed evidence signer fingerprint mismatch")
    try:
        embedded = base64.b64decode(signer["publicKeyBase64"], validate=True)
        sig = base64.b64decode(signature["signatureBase64"], validate=True)
    except Exception as exc:
        raise DeployPackError("invalid signed evidence encoding") from exc
    if embedded != public_raw:
        raise DeployPackError("signed evidence embeds a different public key")
    message = canonical_bytes(payload)
    if signature.get("payloadSha256") != hashlib.sha256(message).hexdigest():
        raise DeployPackError("signed evidence payload SHA-256 mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(sig, message)
    except Exception as exc:
        raise DeployPackError("signed remote evidence signature verification failed") from exc
    return payload


def ingest_signed_remote_evidence(
    signed_path: Path,
    archive: Path,
    public_key_file: Path,
    *,
    root: Path,
    output: Path | None = None,
) -> Path:
    try:
        envelope = json.loads(signed_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeployPackError(f"invalid signed evidence JSON: {signed_path}") from exc
    public_raw = read_public_key_file(public_key_file)
    public_sha256 = public_fingerprint(public_raw)
    payload_identity = (envelope.get("payload") or {}).get("verifierIdentity") or {}
    verifier_id = payload_identity.get("verifierId")
    if not verifier_id:
        raise DeployPackError("signed evidence lacks verifier identity")
    committed = assert_precommitted_signing_key(root, verifier_id, public_sha256)
    if payload_identity.get("expectedPublicKeySha256") != public_sha256:
        raise DeployPackError(
            "signed evidence verifier identity does not carry the precommitted public-key fingerprint"
        )
    payload = verify_envelope(envelope, public_raw)
    temp = signed_path.with_name(signed_path.name + ".payload.tmp.json")
    try:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        normalized = ingest_remote_evidence(temp, archive, output=output)
    finally:
        temp.unlink(missing_ok=True)
    value = json.loads(normalized.read_text(encoding="utf-8"))
    value["assurance"] = assurance_for(verification_scope="remote", signed=True, method=value.get("verificationMethod"))
    value["signedRemoteEvidence"] = {
        "source": str(signed_path),
        "sourceSha256": hashlib.sha256(signed_path.read_bytes()).hexdigest(),
        "algorithm": "Ed25519",
        "publicKeySha256": public_fingerprint(public_raw),
        "payloadSha256": envelope["signature"]["payloadSha256"],
    }
    normalized.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return normalized


def python_signed_verifier(manifest: dict, seed: bytes, public_raw: bytes, verifier_identity: dict) -> str:
    template = r'''#!/usr/bin/env python3
import base64, hashlib, json, stat, sys
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
MANIFEST=json.loads(__MANIFEST__)
VERIFIER_IDENTITY=json.loads(__IDENTITY__)
SEED=base64.b64decode(__SEED__); PUB=base64.b64decode(__PUB__)
def canon(v): return json.dumps(v,separators=(",",":"),sort_keys=True).encode()
def sha(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""): h.update(b)
 return h.hexdigest()
def typ(p): return "symlink" if p.is_symlink() else "file" if p.is_file() else "directory" if p.is_dir() else "other"
def safe(root,rel):
 if not isinstance(rel,str) or not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"): return None
 parts=rel.split("/")
 if any(x in ("",".","..") for x in parts) or (len(rel)>=2 and rel[1]==":" and rel[0].isalpha()): return None
 cur=root
 for x in parts[:-1]:
  cur=cur/x
  if cur.is_symlink(): return None
 return root.joinpath(*parts)
def main():
 root=Path("."); strict=False; out=None; positional=[]; i=1
 while i < len(sys.argv):
  arg=sys.argv[i]
  if arg in ("-h","--help"):
   print(f"usage: {Path(sys.argv[0]).name} [ROOT] --signed-evidence-out FILE [--strict-permissions]")
   print("Verify the deployed tree against the manifest embedded in this one-time deploy-pack verifier.")
   print("ROOT defaults to the current directory and may appear before or after options.")
   print("--signed-evidence-out FILE  Required path for signed verification evidence JSON.")
   print("--strict-permissions        Require exact recorded permission bits.")
   print("Exit 0 only when deployment verification passes and signed evidence is written."); return 0
  if arg=="--strict-permissions": strict=True; i+=1; continue
  if arg=="--signed-evidence-out":
   if i+1>=len(sys.argv): print("ERROR: --signed-evidence-out requires FILE",file=sys.stderr); return 2
   out=Path(sys.argv[i+1]); i+=2; continue
  if arg.startswith("-"):
   print(f"ERROR: unknown option: {arg}",file=sys.stderr); return 2
  positional.append(arg); i+=1
 if len(positional)>1:
  print("ERROR: at most one ROOT may be supplied",file=sys.stderr); return 2
 if positional: root=Path(positional[0])
 root=root.resolve()
 if out is None:
  print("ERROR: --signed-evidence-out is required",file=sys.stderr); return 2
 failures=[]
 for e in MANIFEST.get("files",[]):
  rel=e["path"]; p=safe(root,rel)
  if p is None: failures.append(["PATH_ESCAPE",rel]); continue
  if not p.exists() and not p.is_symlink(): failures.append(["MISSING",rel]); continue
  if typ(p)!=e.get("type","file"): failures.append(["TYPE",rel]); continue
  em=int(e.get("mode","0o0"),8)&0o7777; am=stat.S_IMODE(p.lstat().st_mode)&0o7777
  if (am!=em if strict else (am&0o111)!=(em&0o111)): failures.append(["MODE",rel])
  if e.get("type","file")=="symlink":
   t=str(p.readlink())
   if t!=e.get("symlinkTarget"): failures.append(["LINK_TARGET",rel]); continue
   if hashlib.sha256(t.encode()).hexdigest()!=e["sha256"]: failures.append(["HASH",rel])
  else:
   if p.stat().st_size!=e["size"]: failures.append(["SIZE",rel]); continue
   if sha(p)!=e["sha256"]: failures.append(["HASH",rel])
 for rel in MANIFEST.get("remoteDeletions",[]):
  p=safe(root,rel)
  if p is None: failures.append(["PATH_ESCAPE",rel]); continue
  if p.exists() or p.is_symlink(): failures.append(["DELETE_PENDING",rel])
 if failures:
  print("DEPLOY-PACK VERIFY: FAIL")
  for k,r in failures: print(f"  {k:<14} {r}")
  return 1
 payload={"schemaVersion":1,"verifierIdentity":VERIFIER_IDENTITY,"result":"PASS","verificationScope":"remote","verificationMethod":"ssh-cli","verificationRoot":str(root),"strictPermissions":strict,"verifierRuntime":"python-signed","assurance":{"schemaVersion":1,"level":"host-cooperative-remote","authority":"target-host-self-verification","claims":["remote-deployed-tree-state","manifest-binding","evidence-integrity-after-host-signing"],"doesNotClaim":["host-compromise-resistance","independent-attestation","hardware-rooted-attestation","host-honesty"],"verificationMethod":"ssh-cli","evidenceIntegrity":"ed25519-precommitted-verifier-key"},"verifiedAt":datetime.now(timezone.utc).isoformat(),"manifest":{"sha256":hashlib.sha256(canon(MANIFEST)).hexdigest(),"schemaVersion":MANIFEST.get("schemaVersion"),"baselineRef":MANIFEST.get("baselineRef"),"baselineCommit":MANIFEST.get("baselineCommit"),"headCommit":MANIFEST.get("headCommit"),"fileCount":len(MANIFEST.get("files",[])),"remoteDeletionCount":len(MANIFEST.get("remoteDeletions",[]))}}
 msg=canon(payload); sig=Ed25519PrivateKey.from_private_bytes(SEED).sign(msg)
 env={"schemaVersion":1,"kind":"deploy-pack.remote-evidence.signed","payload":payload,"signature":{"algorithm":"Ed25519","payloadSha256":hashlib.sha256(msg).hexdigest(),"signatureBase64":base64.b64encode(sig).decode()},"signer":{"publicKeyBase64":base64.b64encode(PUB).decode(),"publicKeySha256":hashlib.sha256(PUB).hexdigest()}}
 out.write_text(json.dumps(env,indent=2,sort_keys=True)+"\n")
 print("DEPLOY-PACK VERIFY: PASS"); print(f"  signed evidence: {out}"); return 0
if __name__=="__main__": raise SystemExit(main())
'''
    return (template.replace("__MANIFEST__", repr(json.dumps(manifest, separators=(",", ":"), sort_keys=True)))
                    .replace("__SEED__", repr(base64.b64encode(seed).decode("ascii")))
                    .replace("__PUB__", repr(base64.b64encode(public_raw).decode("ascii")))
                    .replace("__IDENTITY__", repr(json.dumps(verifier_identity,separators=(",",":"),sort_keys=True))))


def php_signed_verifier(manifest: dict, seed: bytes, public_raw: bytes, verifier_identity: dict) -> str:
    _validate_manifest_paths(manifest)
    # libsodium secret-key representation is seed || public key.
    secret64 = seed + public_raw
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True).replace("\\", "\\\\").replace("'", "\\'")
    template = r'''<?php
declare(strict_types=1);
$manifest=json_decode('__MANIFEST__',true,512,JSON_THROW_ON_ERROR);$verifierIdentity=json_decode('__IDENTITY__',true,512,JSON_THROW_ON_ERROR);
$secret=base64_decode('__SECRET__',true); $public=base64_decode('__PUB__',true);
if(!function_exists('sodium_crypto_sign_detached')){fwrite(STDERR,"ERROR: PHP sodium extension required\n");exit(2);}
$root='.';$strict=false;$out=null;$positional=[];
for($i=1;$i<count($argv);$i++){
  $arg=$argv[$i];
  if($arg==='-h'||$arg==='--help'){fwrite(STDOUT,"usage: ".basename($argv[0])." [ROOT] --signed-evidence-out FILE [--strict-permissions]\n\nVerify the deployed tree against the manifest embedded in this one-time deploy-pack verifier.\nROOT defaults to the current directory and may appear before or after options.\n\n  --signed-evidence-out FILE  Required path for signed verification evidence JSON.\n  --strict-permissions        Require exact recorded permission bits.\n\nExit 0 only when deployment verification passes and signed evidence is written.\n");exit(0);}
  if($arg==='--strict-permissions'){$strict=true;continue;}
  if($arg==='--signed-evidence-out'){if(!isset($argv[$i+1])){fwrite(STDERR,"ERROR: --signed-evidence-out requires FILE\n");exit(2);}$out=$argv[++$i];continue;}
  if(str_starts_with($arg,'-')){fwrite(STDERR,"ERROR: unknown option: ".$arg."\n");exit(2);}
  $positional[]=$arg;
}
if(count($positional)>1){fwrite(STDERR,"ERROR: at most one ROOT may be supplied\n");exit(2);}
if(count($positional)===1)$root=$positional[0];$root=realpath($root)?:$root;
if($out===null){fwrite(STDERR,"ERROR: --signed-evidence-out is required\n");exit(2);}$fail=[];
function dpt($p){if(is_link($p))return'symlink';if(is_file($p))return'file';if(is_dir($p))return'directory';return'other';}
function dpsafe($root,$rel){if(!is_string($rel)||$rel===''||strpos($rel,"\0")!==false||strpos($rel,'\\')!==false||str_starts_with($rel,'/')||preg_match('/^[A-Za-z]:/',$rel))return null;$parts=explode('/',$rel);$cur=rtrim($root,DIRECTORY_SEPARATOR);for($i=0;$i<count($parts);$i++){if($parts[$i]===''||$parts[$i]==='.'||$parts[$i]==='..')return null;if($i<count($parts)-1){$cur.=DIRECTORY_SEPARATOR.$parts[$i];if(is_link($cur))return null;}}return rtrim($root,DIRECTORY_SEPARATOR).DIRECTORY_SEPARATOR.implode(DIRECTORY_SEPARATOR,$parts);}
foreach($manifest['files']??[] as $e){$rel=$e['path'];$p=dpsafe($root,$rel);if($p===null){$fail[]=['PATH_ESCAPE',$rel];continue;}if(!file_exists($p)&&!is_link($p)){$fail[]=['MISSING',$rel];continue;}if(dpt($p)!==($e['type']??'file')){$fail[]=['TYPE',$rel];continue;}$em=intval(substr($e['mode']??'0o0',2),8)&07777;$am=fileperms($p)&07777;if($strict?($am!==$em):(($am&0111)!==($em&0111)))$fail[]=['MODE',$rel];if(($e['type']??'file')==='symlink'){$t=readlink($p);if($t!==($e['symlinkTarget']??null)){$fail[]=['LINK_TARGET',$rel];continue;}if(hash('sha256',$t)!==$e['sha256'])$fail[]=['HASH',$rel];}else{if(filesize($p)!==$e['size']){$fail[]=['SIZE',$rel];continue;}if(hash_file('sha256',$p)!==$e['sha256'])$fail[]=['HASH',$rel];}}
foreach($manifest['remoteDeletions']??[] as $rel){$p=dpsafe($root,$rel);if($p===null){$fail[]=['PATH_ESCAPE',$rel];continue;}if(file_exists($p)||is_link($p))$fail[]=['DELETE_PENDING',$rel];}
if($fail){fwrite(STDOUT,"DEPLOY-PACK VERIFY: FAIL\n");foreach($fail as [$k,$r])fwrite(STDOUT,sprintf("  %-14s %s\n",$k,$r));exit(1);}
$payload=['assurance'=>['authority'=>'target-host-self-verification','claims'=>['remote-deployed-tree-state','manifest-binding','evidence-integrity-after-host-signing'],'doesNotClaim'=>['host-compromise-resistance','independent-attestation','hardware-rooted-attestation','host-honesty'],'evidenceIntegrity'=>'ed25519-precommitted-verifier-key','level'=>'host-cooperative-remote','schemaVersion'=>1,'verificationMethod'=>'ssh-cli'],'manifest'=>['baselineCommit'=>$manifest['baselineCommit']??null,'baselineRef'=>$manifest['baselineRef']??null,'fileCount'=>count($manifest['files']??[]),'headCommit'=>$manifest['headCommit']??null,'remoteDeletionCount'=>count($manifest['remoteDeletions']??[]),'schemaVersion'=>$manifest['schemaVersion']??null,'sha256'=>hash('sha256',json_encode($manifest,JSON_UNESCAPED_SLASHES))],'result'=>'PASS','schemaVersion'=>1,'strictPermissions'=>$strict,'verificationMethod'=>'ssh-cli','verificationRoot'=>$root,'verificationScope'=>'remote','verifiedAt'=>gmdate('c'),'verifierIdentity'=>$verifierIdentity,'verifierRuntime'=>'php-sodium-signed'];
$msg=json_encode($payload,JSON_UNESCAPED_SLASHES);$sig=sodium_crypto_sign_detached($msg,$secret);$env=['kind'=>'deploy-pack.remote-evidence.signed','payload'=>$payload,'schemaVersion'=>1,'signature'=>['algorithm'=>'Ed25519','payloadSha256'=>hash('sha256',$msg),'signatureBase64'=>base64_encode($sig)],'signer'=>['publicKeyBase64'=>base64_encode($public),'publicKeySha256'=>hash('sha256',$public)]];file_put_contents($out,json_encode($env,JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES)."\n");fwrite(STDOUT,"DEPLOY-PACK VERIFY: PASS\n  signed evidence: ".$out."\n");exit(0);
'''
    return (template.replace("__MANIFEST__", payload)
                    .replace("__SECRET__", base64.b64encode(secret64).decode("ascii"))
                    .replace("__PUB__", base64.b64encode(public_raw).decode("ascii"))
                    .replace("__IDENTITY__", json.dumps(verifier_identity,separators=(",",":"),sort_keys=True).replace("\\","\\\\").replace("'","\\'")))


def write_signed_remote_verifier(
    archive: Path,
    language: str,
    verifier_identity: dict,
    output: Path | None = None,
    *,
    root: Path,
) -> tuple[Path, Path]:
    manifest = load_manifest_from_archive(archive)
    seed, public = generate_ephemeral_keypair()
    verifier_id = verifier_identity.get("verifierId")
    if not verifier_id:
        raise DeployPackError("signed verifier requires an issued verifier identity")
    verifier_identity = precommit_signing_key(root, verifier_id, public_fingerprint(public))
    if language == "php":
        content, suffix = php_signed_verifier(manifest, seed, public, verifier_identity), ".verify-signed.php"
    elif language == "python":
        content, suffix = python_signed_verifier(manifest, seed, public, verifier_identity), ".verify-signed.py"
    else:
        raise DeployPackError("signed verifier supports php or python")
    if output is None:
        output = archive.with_name(archive.name + suffix)
    output.write_text(content, encoding="utf-8")
    # HARDEN-13: both generated variants embed private signing material.
    output.chmod(0o700 if language == "python" else 0o600)
    public_key = output.with_suffix(output.suffix + ".public-key.json")
    write_public_key_file(public_key, public)
    return output, public_key
