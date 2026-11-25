"""P4C bmv2stf's terminate/wait contract applies to native tool wrappers.

The caller must observe the native tool's Unix signal status and completion,
including reaping the native tool before returning. Exercise real processes;
a wrapper that exits immediately while its child survives fails this contract.
"""
import os
import select
import signal
import subprocess
import sys
import unittest


class SignalContract(unittest.TestCase):
    def test_native_tool_lifecycle(self):
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
            with self.subTest(signal=signum):
                process = subprocess.Popen([sys.argv[1]], stdout=subprocess.PIPE, text=True)
                child = None
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 30)[0], "native tool did not become ready")
                    child = int(process.stdout.readline())
                    process.send_signal(signum)
                    self.assertEqual(process.wait(timeout=30), -signum)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child, 0)
                    child = None
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    process.stdout.close()
                    if child is not None:
                        try:
                            os.kill(child, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
