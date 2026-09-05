from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "module_name_normalizer.py"
spec = importlib.util.spec_from_file_location("module_name_normalizer", MODULE)
mnn = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mnn
assert spec.loader
spec.loader.exec_module(mnn)

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>x</groupId>
    <artifactId>parent-artifact</artifactId>
    <version>1</version>
  </parent>
  <artifactId>customer-api</artifactId>
  <name>customer-api</name>
  <dependencies>
    <dependency>
      <groupId>x</groupId>
      <artifactId>dependency-artifact</artifactId>
      <version>1</version>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>x</groupId>
        <artifactId>plugin-artifact</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
"""

class NameTest(unittest.TestCase):
    def test_title(self):
        self.assertEqual("Financial Operations", mnn.title_name("financial_operations"))
        self.assertEqual("Customer Api", mnn.title_name("customer-api"))

    def test_prefix_title(self):
        self.assertEqual("Tembeek Customer Api", mnn.desired_name("customer-api","prefix-title","Tembeek"))

class PomSafetyTest(unittest.TestCase):
    def test_only_project_name_changes(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"pom.xml"
            path.write_text(POM,encoding="utf-8")
            change=mnn.plan_pom(path,"title",None,True)
            self.assertIsNotNone(change)
            mnn.write_pom(path,change.new_name)

            tree=ET.parse(path)
            root=tree.getroot()
            def lname(tag): return tag.rsplit("}",1)[-1]

            direct={lname(c.tag):(c.text or "").strip() for c in list(root)}
            self.assertEqual("customer-api",direct["artifactId"])
            self.assertEqual("Customer Api",direct["name"])

            artifacts=[
                (el.text or "").strip()
                for el in root.iter()
                if lname(el.tag)=="artifactId"
            ]
            self.assertIn("parent-artifact",artifacts)
            self.assertIn("dependency-artifact",artifacts)
            self.assertIn("plugin-artifact",artifacts)

    def test_missing_name_added_after_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"pom.xml"
            path.write_text("<project><modelVersion>4.0.0</modelVersion><artifactId>a-b</artifactId></project>")
            change=mnn.plan_pom(path,"title",None,True)
            self.assertEqual("add",change.action)
            mnn.write_pom(path,change.new_name)
            root=ET.parse(path).getroot()
            tags=[mnn.local_name(c.tag) for c in list(root)]
            self.assertEqual(["modelVersion","artifactId","name"],tags)

    def test_no_add_missing(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"pom.xml"
            path.write_text("<project><artifactId>a-b</artifactId></project>")
            self.assertIsNone(mnn.plan_pom(path,"title",None,False))

class DiscoveryTest(unittest.TestCase):
    def test_generated_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"pom.xml").write_text("<project><artifactId>root</artifactId></project>")
            target=root/"target"
            target.mkdir()
            (target/"pom.xml").write_text("<project><artifactId>generated</artifactId></project>")
            found=mnn.discover_poms(root)
            self.assertEqual([root/"pom.xml"],found)

    def test_alias_root_preserves_lexical_discovery_path(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            real=base/"real"
            real.mkdir()
            (real/"pom.xml").write_text("<project><artifactId>root</artifactId></project>")
            alias=base/"alias"
            alias.symlink_to(real,target_is_directory=True)
            found=mnn.discover_poms(alias)
            self.assertEqual([alias/"pom.xml"],found)

class CliTest(unittest.TestCase):
    def run_cli(self,*args):
        return subprocess.run(["python3",str(MODULE),*args],text=True,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    def test_help_version(self):
        cp=self.run_cli("--version")
        self.assertEqual(0,cp.returncode)
        self.assertIn("module-name-normalizer 1.0.1",cp.stdout)
        cp=self.run_cli("--help")
        self.assertEqual(0,cp.returncode)
        for flag in ("--write","--check","--style","--prefix","--add-missing"):
            self.assertIn(flag,cp.stdout)

    def test_check_and_write(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pom=root/"pom.xml"
            pom.write_text("<project><artifactId>a-b</artifactId><name>a-b</name></project>")

            cp=self.run_cli(str(root),"--check")
            self.assertEqual(1,cp.returncode)
            self.assertIn("<name>a-b</name>",pom.read_text())

            cp=self.run_cli(str(root),"--write")
            self.assertEqual(0,cp.returncode,cp.stderr)
            self.assertIn("A B",pom.read_text())

            cp=self.run_cli(str(root),"--check")
            self.assertEqual(0,cp.returncode)

if __name__=="__main__":
    unittest.main()
