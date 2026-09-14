from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deploy_pack.cli import parser


class HelpColorAlias01Tests(unittest.TestCase):
    def test_color_always_styles_top_level_and_nested_help(self):
        with patch.dict(os.environ, {"DEPLOY_PACK_COLOR":"always"}, clear=False):
            top = parser().format_help()
            nested = parser()._subparsers._group_actions[0].choices["recovery"].format_help()
        self.assertIn("\x1b[", top)
        self.assertIn("\x1b[", nested)
        self.assertIn("SHORT COMMAND", top)
        self.assertIn("dp", top)

    def test_no_color_wins(self):
        with patch.dict(os.environ, {"DEPLOY_PACK_COLOR":"always", "NO_COLOR":"1"}, clear=False):
            text = parser().format_help()
        self.assertNotIn("\x1b[", text)

    def test_never_is_plain(self):
        env=dict(os.environ)
        env.pop("NO_COLOR",None)
        env["DEPLOY_PACK_COLOR"]="never"
        with patch.dict(os.environ, env, clear=True):
            p=parser()
            text=p.format_help()
        self.assertFalse(getattr(p, "color", False))
        self.assertNotIn("\x1b[",text)

    def test_argparse_native_color_disabled_across_parser_tree(self):
        def walk(p):
            yield p
            for action in getattr(p, "_actions", ()):
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        yield from walk(child)

        import argparse
        for p in walk(parser()):
            self.assertFalse(getattr(p, "color", False), p.prog)

    def test_bootstrap_has_non_overwrite_guard(self):
        text=(ROOT / "scripts" / "bootstrap-runtime.sh").read_text(encoding="utf-8")
        self.assertIn("DEPLOY_PACK_SHORT_COMMAND",text)
        self.assertIn("refusing to overwrite unrelated",text)
        self.assertIn("bin/deploy-pack",text)


if __name__ == "__main__":
    unittest.main()
