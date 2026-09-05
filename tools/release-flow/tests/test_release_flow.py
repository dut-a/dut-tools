import importlib.util, subprocess, sys, tempfile, unittest
from pathlib import Path
M=Path(__file__).resolve().parents[1]/'src'/'release_flow.py'; s=importlib.util.spec_from_file_location('rf',M); rf=importlib.util.module_from_spec(s); sys.modules[s.name]=rf; s.loader.exec_module(rf)
class T(unittest.TestCase):
 def test_derive(self): self.assertEqual('1.5.0-SNAPSHOT',rf.derive('1.4.3','minor','SNAPSHOT'))
 def test_cli(self):
  with tempfile.TemporaryDirectory() as td:
   r=Path(td); (r/'VERSION').write_text('1.0.0\n'); (r/'.release-flow.toml').write_text('version=1\n[[target]]\nname="v"\ntype="plain"\npath="VERSION"\n')
   self.assertEqual(0,subprocess.run(['python3',str(M),'prepare','1.1.0','--root',str(r)]).returncode); self.assertEqual('1.0.0\n',(r/'VERSION').read_text())
   self.assertEqual(0,subprocess.run(['python3',str(M),'prepare','1.1.0','--root',str(r),'--write']).returncode); self.assertEqual('1.1.0\n',(r/'VERSION').read_text())
   self.assertEqual(0,subprocess.run(['python3',str(M),'verify','1.1.0','--root',str(r)]).returncode)
 def test_help_version(self):
  self.assertEqual(0,subprocess.run(['python3',str(M),'--help']).returncode); cp=subprocess.run(['python3',str(M),'--version'],text=True,stdout=subprocess.PIPE); self.assertIn('1.0.0',cp.stdout)

 def test_transactional_restore(self):
  with tempfile.TemporaryDirectory() as td:
   r=Path(td); (r/'A').write_text('a'); (r/'B').write_text('b')
   ta=rf.Target('A','plain','A'); tb=rf.Target('B','plain','B')
   changes=[(ta,'a','A2',True),(tb,'b','B2',True)]
   original=rf.atomic; calls={'n':0}
   def fail_second(path,content):
    calls['n']+=1
    if calls['n']==2: raise OSError('simulated second-write failure')
    return original(path,content)
   rf.atomic=fail_second
   try:
    with self.assertRaises(OSError): rf.apply_changes_transactionally(r,changes)
   finally: rf.atomic=original
   self.assertEqual('a',(r/'A').read_text()); self.assertEqual('b',(r/'B').read_text())
if __name__=='__main__': unittest.main()
