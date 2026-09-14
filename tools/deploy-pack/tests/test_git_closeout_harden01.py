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
