import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "deploy-store-demo.sh"


class StoreDeployerTests(unittest.TestCase):
    def run_deployer(self, arguments, api_server="https://approved.example.test",
                     sample=True, fail_match="", public_ip="203.0.113.10"):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            log = directory / "calls"
            manifests = directory / "manifests"
            kubectl = directory / "kubectl"
            kubectl.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
                'if [[ -n "$FAIL_MATCH" && "$*" == *"$FAIL_MATCH"* ]]; then exit 1; fi\n'
                'if [[ "$*" == *"config view"* ]]; then printf "%s" "$MOCK_API"; fi\n'
                'if [[ "$*" == *"get ingress sre-web"* && "$HAS_SAMPLE" == 1 ]]; then printf ingress/sre-web; fi\n'
                'if [[ "$*" == *" kustomize "* ]]; then printf "apiVersion: v1\\nkind: List\\nitems: []\\n"; fi\n'
                'if [[ "$*" == *" apply "* ]]; then\n'
                '  for argument in "$@"; do\n'
                '    if [[ -f "$argument" ]]; then cat "$argument" >> "$MANIFEST_LOG"; fi\n'
                '  done\n'
                'fi\n'
            )
            kubectl.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": f"{directory}:{os.environ['PATH']}",
                "CALL_LOG": str(log),
                "MANIFEST_LOG": str(manifests),
                "MOCK_API": api_server,
                "HAS_SAMPLE": "1" if sample else "0",
                "FAIL_MATCH": fail_match,
                "KUBECONFIG": str(directory / "kubeconfig"),
                "EXPECTED_CONTEXT": "approved-context",
                "AKS_FQDN": "approved.example.test",
                "APPGW_PUBLIC_IP": public_ip,
            }
            result = subprocess.run(
                ["bash", str(SCRIPT), *arguments], env=environment,
                capture_output=True, text=True, check=False,
            )
            calls = log.read_text().splitlines() if log.exists() else []
            payload = manifests.read_text() if manifests.exists() else ""
            return result, calls, payload

    def test_requires_explicit_apply(self):
        result, calls, _ = self.run_deployer([])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_rejects_invalid_ip_before_cluster_access(self):
        result, calls, _ = self.run_deployer(["--apply"], public_ip="999.1.1.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_rejects_wrong_cluster(self):
        result, calls, _ = self.run_deployer(["--apply"], api_server="https://wrong.example.test")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_requires_route_takeover_authorization(self):
        result, calls, _ = self.run_deployer(["--apply"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(" apply " in call or " delete " in call for call in calls))

    def test_failed_dry_run_stops_writes(self):
        result, calls, _ = self.run_deployer(["--apply"], sample=False, fail_match="--dry-run=server")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(" apply -f " in call or " delete " in call for call in calls))

    def test_certificate_failure_preserves_sample_route(self):
        result, calls, _ = self.run_deployer(
            ["--apply", "--replace-sample-ingress"], fail_match="certificate/store-front",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(" delete " in call or "ingress.yaml" in call for call in calls))

    def test_ip_certificate_precedes_route_takeover(self):
        result, calls, payload = self.run_deployer(["--apply", "--replace-sample-ingress"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call.startswith("--context approved-context ") for call in calls))
        self.assertIn('"ipAddresses": [\n      "203.0.113.10"', payload)
        certificate_wait = next(index for index, call in enumerate(calls) if "certificate/store-front" in call)
        route_delete = next(index for index, call in enumerate(calls) if " delete ingress " in call)
        route_apply = next(index for index, call in enumerate(calls) if " apply -f " in call and "ingress.yaml" in call)
        self.assertLess(certificate_wait, route_delete)
        self.assertLess(route_delete, route_apply)

    def test_clean_deployment_does_not_delete_routes(self):
        result, calls, _ = self.run_deployer(["--apply"], sample=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(" delete " in call for call in calls))


if __name__ == "__main__":
    unittest.main()