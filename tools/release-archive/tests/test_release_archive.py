from __future__ import annotations
import hashlib, json, os, subprocess, tempfile, unittest, zipfile, tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
CLI=ROOT/"bin/release-archive"

def run(*args,cwd=None):
    return subprocess.run([str(CLI),*map(str,args)],cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

def init_repo(root):
    subprocess.run(["git","init","-q"],cwd=root,check=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=root,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=root,check=True)

class ReleaseArchiveTest(unittest.TestCase):
    def test_version_help(self):
        self.assertEqual(0,run("--version").returncode)
        h=run("--help"); self.assertEqual(0,h.returncode); self.assertIn("tar.gz",h.stdout); self.assertIn("--check",h.stdout)

    def test_zip_and_targz_same_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"proj"; root.mkdir(); init_repo(root)
            (root/"README.md").write_text("hello\n")
            (root/"src").mkdir(); (root/"src/a.txt").write_text("A\n")
            subprocess.run(["git","add","."],cwd=root,check=True); subprocess.run(["git","commit","-qm","init"],cwd=root,check=True)
            base=Path(td)/"dist/release"
            cp=run(root,"--format","both","--output",base,"--source-date-epoch","0")
            self.assertEqual(0,cp.returncode,cp.stderr)
            zp=Path(str(base)+".zip"); tp=Path(str(base)+".tar.gz")
            self.assertTrue(zp.exists()); self.assertTrue(tp.exists())
            with zipfile.ZipFile(zp) as z: zn=set(z.namelist()); zm=json.loads(z.read("RELEASE-MANIFEST.json"))
            with tarfile.open(tp,"r:gz") as t: tn=set(t.getnames()); tm=json.loads(t.extractfile("RELEASE-MANIFEST.json").read())
            self.assertEqual(zn,tn); self.assertEqual(zm,tm)
            self.assertEqual({"README.md","src/a.txt","RELEASE-MANIFEST.json"},zn)
            self.assertTrue(Path(str(zp)+".sha256").exists()); self.assertTrue(Path(str(tp)+".sha256").exists())

    def test_reproducible_zip(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"proj"; root.mkdir(); init_repo(root)
            (root/"a.txt").write_text("A")
            subprocess.run(["git","add","."],cwd=root,check=True); subprocess.run(["git","commit","-qm","init"],cwd=root,check=True)
            a=Path(td)/"a"; b=Path(td)/"b"
            self.assertEqual(0,run(root,"--output",a,"--source-date-epoch","0").returncode)
            self.assertEqual(0,run(root,"--output",b,"--source-date-epoch","0").returncode)
            self.assertEqual(hashlib.sha256(Path(str(a)+".zip").read_bytes()).hexdigest(),hashlib.sha256(Path(str(b)+".zip").read_bytes()).hexdigest())

    def test_dirty_refused_and_allow_dirty_works(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"proj"; root.mkdir(); init_repo(root)
            (root/"a.txt").write_text("A")
            subprocess.run(["git","add","."],cwd=root,check=True); subprocess.run(["git","commit","-qm","init"],cwd=root,check=True)
            (root/"a.txt").write_text("B")
            bad=run(root,"--check"); self.assertNotEqual(0,bad.returncode); self.assertIn("dirty",bad.stderr.lower())
            good=run(root,"--check","--allow-dirty"); self.assertEqual(0,good.returncode,good.stderr)

    def test_untracked_excluded_unless_requested(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"proj"; root.mkdir(); init_repo(root)
            (root/"tracked.txt").write_text("T")
            subprocess.run(["git","add","."],cwd=root,check=True); subprocess.run(["git","commit","-qm","init"],cwd=root,check=True)
            (root/"extra.txt").write_text("U")
            base=Path(td)/"release"
            cp=run(root,"--output",base,"--allow-dirty"); self.assertEqual(0,cp.returncode,cp.stderr)
            with zipfile.ZipFile(Path(str(base)+".zip")) as z: self.assertNotIn("extra.txt",z.namelist())
            base2=Path(td)/"release2"
            cp=run(root,"--output",base2,"--allow-dirty","--include-untracked"); self.assertEqual(0,cp.returncode,cp.stderr)
            with zipfile.ZipFile(Path(str(base2)+".zip")) as z: self.assertIn("extra.txt",z.namelist())

if __name__=="__main__": unittest.main()
