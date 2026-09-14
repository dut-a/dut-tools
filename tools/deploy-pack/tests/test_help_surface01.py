import contextlib, io, unittest
from deploy_pack.cli import parser

class HelpSurface01Tests(unittest.TestCase):
    def help_for(self,*argv):
        buf=io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(buf):
            parser().parse_args([*argv,'--help'])
        self.assertEqual(cm.exception.code,0)
        return buf.getvalue()

    def assertHelp(self, argv, *needles):
        text=self.help_for(*argv)
        for needle in needles:
            self.assertIn(needle,text, f"{' '.join(argv)} help missing {needle!r}")

    def test_core_operator_help(self):
        self.assertHelp(('inspect',),'deployment surface','deployment ceiling')
        self.assertHelp(('baseline',),'currently running in production')
        self.assertHelp(('verify',),'local verification','remote-verifier')
        self.assertHelp(('remote-verifier',),'SIGNED WORKFLOW','public-key')
        self.assertHelp(('ingest-signed-remote-evidence',),'Public-key sidecar','normalized')
        self.assertHelp(('history-verify',),'hash-chain','post-closeout')

    def test_lifecycle_help(self):
        self.assertHelp(('verifier',),'short-lived verifier identities')
        self.assertHelp(('verifier','issue'),'fresh verifier identity')
        self.assertHelp(('verifier','show'),'lifecycle')
        self.assertHelp(('keys','revoke'),'audit reason')
        self.assertHelp(('rollback','plan'),'normal evidence/mark path')

    def test_recovery_help(self):
        self.assertHelp(('recovery',),'offline custody','SAFETY')
        self.assertHelp(('recovery','export'),'Signed export')
        self.assertHelp(('recovery','trust'),'offline trust-anchor custody')
        self.assertHelp(('recovery','trust','verify-quorum'),'quorum')
        self.assertHelp(('recovery','trust','checkpoint-verify'),'checkpoint')

    def test_reconciliation_is_registered_and_documented(self):
        p=parser()
        sub=next(a for a in p._actions if getattr(a,'choices',None) and 'mark' in a.choices)
        self.assertIn('reconcile-baseline', sub.choices)
        self.assertHelp(('reconcile-baseline',),'audited reconciliation','fresh signed remote evidence')
