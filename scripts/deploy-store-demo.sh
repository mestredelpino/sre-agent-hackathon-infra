#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
replace_sample=false
if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: KUBECONFIG=<file> EXPECTED_CONTEXT=<context> AKS_FQDN=<api-host> APPGW_PUBLIC_IP=<IPv4> bash scripts/deploy-store-demo.sh --apply [--replace-sample-ingress]\n'
  exit 0
fi
if [[ "$#" -lt 1 || "$#" -gt 2 || "$1" != "--apply" ]]; then
  printf 'Explicit --apply required; use --help for prerequisites.\n' >&2
  exit 1
fi
if [[ "$#" -eq 2 ]]; then
  if [[ "$2" != "--replace-sample-ingress" ]]; then
    printf 'Unknown option: %s\n' "$2" >&2
    exit 1
  fi
  replace_sample=true
fi
: "${KUBECONFIG:?Set the dedicated cluster kubeconfig}"
: "${EXPECTED_CONTEXT:?Set the approved cluster context}"
: "${AKS_FQDN:?Set the approved API hostname from Azure}"
: "${APPGW_PUBLIC_IP:?Set the Application Gateway public IPv4 address from Azure}"
command -v kubectl >/dev/null
command -v jq >/dev/null
if ! jq -en --arg ip "$APPGW_PUBLIC_IP" '$ip | test("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$") and (split(".") | all(.[]; tonumber >= 0 and tonumber <= 255))' >/dev/null; then
  printf 'APPGW_PUBLIC_IP must be an IPv4 address.\n' >&2
  exit 1
fi
api_server="$(kubectl --context "$EXPECTED_CONTEXT" config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
if [[ "$api_server" != "https://$AKS_FQDN" && "$api_server" != "https://$AKS_FQDN:443" ]]; then
  printf 'API server mismatch; no cluster changes made.\n' >&2
  exit 1
fi
kubectl --context "$EXPECTED_CONTEXT" -n cert-manager rollout status deployment/cert-manager --timeout=120s
kubectl --context "$EXPECTED_CONTEXT" -n cert-manager rollout status deployment/cert-manager-webhook --timeout=120s
sample_ingress="$(kubectl --context "$EXPECTED_CONTEXT" -n sre-hackathon get ingress sre-web --ignore-not-found -o name)"
if [[ -n "$sample_ingress" && "$replace_sample" != true ]]; then
  printf 'Sample ingress exists. Review the route takeover and rerun with --replace-sample-ingress.\n' >&2
  exit 1
fi
rendered="$(mktemp)"
certificate=""
trap 'rm -f -- "$rendered" "$certificate"' EXIT
certificate="$(mktemp)"
jq --arg ip "$APPGW_PUBLIC_IP" '.spec.ipAddresses = [$ip]' "$repo_dir/cluster/store-demo/certificate.json" > "$certificate"
kubectl --context "$EXPECTED_CONTEXT" kustomize "$repo_dir/cluster/store-demo" > "$rendered"
kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -f "$repo_dir/cluster/store-demo/namespace.yaml"
kubectl --context "$EXPECTED_CONTEXT" apply -f "$repo_dir/cluster/store-demo/namespace.yaml"
kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -f "$rendered"
kubectl --context "$EXPECTED_CONTEXT" apply -f "$rendered"
kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -f "$certificate"
kubectl --context "$EXPECTED_CONTEXT" apply -f "$certificate"
kubectl --context "$EXPECTED_CONTEXT" -n aks-store-demo wait --for=condition=Ready certificate/store-front --timeout=180s
kubectl --context "$EXPECTED_CONTEXT" -n aks-store-demo rollout status deployment/store-front --timeout=600s
kubectl --context "$EXPECTED_CONTEXT" apply --dry-run=server -f "$repo_dir/cluster/store-demo/ingress.yaml"
if [[ -n "$sample_ingress" ]]; then
  kubectl --context "$EXPECTED_CONTEXT" -n sre-hackathon delete ingress sre-web
fi
kubectl --context "$EXPECTED_CONTEXT" apply -f "$repo_dir/cluster/store-demo/ingress.yaml"
kubectl --context "$EXPECTED_CONTEXT" -n aks-store-demo get pods,certificate,ingress
printf 'Store Demo submitted to AGIC at https://%s/. Verify gateway HTTPS-only listeners and application health.\n' "$APPGW_PUBLIC_IP"
printf 'No DNS entry is required. The lab certificate is self-signed, not publicly trusted.\n'