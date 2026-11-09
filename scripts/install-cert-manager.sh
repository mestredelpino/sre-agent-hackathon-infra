#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
manifest_url="https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml"

if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: KUBECONFIG=<dedicated-file> EXPECTED_CONTEXT=<context> AKS_FQDN=<approved-api-host> bash scripts/install-cert-manager.sh --apply\n'
  printf 'Review the pinned upstream manifest and cluster/cert-manager before authorizing installation.\n'
  exit 0
fi

if [[ "$#" -ne 1 || "$1" != "--apply" ]]; then
  printf 'Explicit --apply required; use --help for prerequisites.\n' >&2
  exit 1
fi

: "${KUBECONFIG:?Set the dedicated cluster kubeconfig}"
: "${EXPECTED_CONTEXT:?Set the approved cluster context}"
: "${AKS_FQDN:?Set the approved API hostname from Azure}"
command -v kubectl >/dev/null

api_server="$(kubectl --context "$EXPECTED_CONTEXT" config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
if [[ "$api_server" != "https://$AKS_FQDN" && "$api_server" != "https://$AKS_FQDN:443" ]]; then
  printf 'API server mismatch; no cluster changes made.\n' >&2
  exit 1
fi

kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -f "$manifest_url"
kubectl --context "$EXPECTED_CONTEXT" apply -f "$manifest_url"
kubectl --context "$EXPECTED_CONTEXT" wait --for=condition=Established --timeout=120s \
  crd/certificates.cert-manager.io crd/issuers.cert-manager.io
for deployment in cert-manager cert-manager-cainjector cert-manager-webhook; do
  kubectl --context "$EXPECTED_CONTEXT" -n cert-manager rollout status "deployment/$deployment" --timeout=300s
done

kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -f "$repo_dir/cluster/cert-manager/namespace.yaml"
kubectl --context "$EXPECTED_CONTEXT" apply -f "$repo_dir/cluster/cert-manager/namespace.yaml"
kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -k "$repo_dir/cluster/cert-manager"
kubectl --context "$EXPECTED_CONTEXT" apply -k "$repo_dir/cluster/cert-manager"
kubectl --context "$EXPECTED_CONTEXT" -n cert-manager-lab wait --for=condition=Ready issuer/lab-selfsigned --timeout=120s
kubectl --context "$EXPECTED_CONTEXT" -n cert-manager-lab wait --for=condition=Ready certificate/sre-example --timeout=180s
printf 'cert-manager installed and lab certificate Ready. Existing ingress and gateway certificates were not changed.\n'