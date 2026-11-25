"""Original empty dh_shlibdeps override agrees with real dh_gencontrol."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from debian.deb822 import Deb822
from runtime_packages import tool_environment

METADATA,NORMAL,PAYLOAD,TOOLS,CONTROL,CHANGELOG=map(Path,sys.argv[1:7])
ARCHITECTURE=sys.argv[7];del sys.argv[1:]
class EmptyShlibdeps(unittest.TestCase):
 def test_real_debhelper_control_and_preserved_elf_evidence(self):
  fields=json.loads(METADATA.read_text())['packages']['metadata-library']
  self.assertEqual(fields['control_fields']['Architecture'],ARCHITECTURE)
  self.assertFalse(fields['shlibdeps']);self.assertTrue(any('libc.so.6' in deps for deps in fields['elf_needed'].values()))
  self.assertNotIn('Depends',fields['control_fields'])
  self.assertIn('libc6',json.loads(NORMAL.read_text())['packages']['metadata-library']['control_fields']['Depends'])
  with tempfile.TemporaryDirectory() as name:
   scratch=Path(name);debian=scratch/'debian';debian.mkdir();shutil.copyfile(CONTROL,debian/'control');shutil.copyfile(CHANGELOG,debian/'changelog');(debian/'compat').write_text('11\n')
   root=debian/'metadata-library';root.mkdir()
   with tarfile.open(PAYLOAD) as archive:archive.extractall(root,filter='data')
   (root/'DEBIAN').mkdir(exist_ok=True)
   # Equivalent to original Debian rules' empty override, followed by dh_gencontrol.
   done=subprocess.run([str(TOOLS.resolve()/'bin/dh_gencontrol'),'-pmetadata-library'],cwd=scratch,env=tool_environment(TOOLS.resolve(),scratch,ARCHITECTURE),capture_output=True,text=True)
   self.assertEqual(done.returncode,0,done.stdout+done.stderr)
   actual=Deb822((root/'DEBIAN/control').read_text())
   for field in ['Package','Version','Architecture','Depends','Pre-Depends','Suggests','Description']:
    self.assertEqual(actual.get(field),fields['control_fields'].get(field),field)
if __name__=='__main__':unittest.main()
