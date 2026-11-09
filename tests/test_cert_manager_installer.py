import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "install-cert-manager.sh"


class CertManagerInstallerTests(unittest.TestCase):
    def run_installer(self, arguments, api_server="https://approved.example.test", fail_dry_run=False):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            log = directory / "calls"
            kubectl = directory / "kubectl"
            kubectl.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
                'if [[ "$*" == *"config view"* ]]; then printf "%s" "$MOCK_API"; fi\n'
                'if [[ "$FAIL_DRY_RUN" == "1" && "$*" == *"--dry-run=server"* ]]; then exit 1; fi\n'
            )
            kubectl.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": f"{directory}:{os.environ['PATH']}",
                "CALL_LOG": str(log),
                "MOCK_API": api_server,
                "FAIL_DRY_RUN": "1" if fail_dry_run else "0",
                "KUBECONFIG": str(directory / "kubeconfig"),
                "EXPECTED_CONTEXT": "approved-context",
                "AKS_FQDN": "approved.example.test",
            }
            result = subprocess.run(
                ["bash", str(SCRIPT), *arguments],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls

    def test_requires_explicit_apply(self):
        result, calls = self.run_installer([])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_rejects_wrong_cluster_before_writes(self):
        result, calls = self.run_installer(["--apply"], api_server="https://wrong.example.test")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("apply", calls[0])

    def test_stops_on_failed_dry_run(self):
        result, calls = self.run_installer(["--apply"], fail_dry_run=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("--dry-run=server", calls[-1])

    def test_installs_and_waits_for_certificate(self):
        result, calls = self.run_installer(["--apply"], api_server="https://approved.example.test:443")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call.startswith("--context approved-context ") for call in calls))
        self.assertIn("v1.21.3/cert-manager.yaml", calls[1])
        self.assertIn("--dry-run=server", calls[1])
        self.assertIn("certificate/sre-example", calls[-1])
        self.assertTrue(any("deployment/cert-manager-webhook" in call for call in calls))


if __name__ == "__main__":
    unittest.main()