#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
hostname="${1:-sre.example.test}"
certificate_dir="$repo_dir/.certs"

if [[ ! "$hostname" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]]; then
  printf 'Provide a DNS hostname.\n' >&2
  exit 1
fi

if [[ -e "$certificate_dir" ]]; then
  printf 'Certificate directory already exists; preserve it or move it securely before generating a replacement.\n' >&2
  exit 1
fi

command -v openssl >/dev/null
mkdir -m 700 -- "$certificate_dir"
openssl rand -out "$certificate_dir/pfx-password" -hex 32
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 90 \
  -keyout "$certificate_dir/appgw.key" \
  -out "$certificate_dir/appgw.crt" \
  -subj "/CN=$hostname" \
  -addext "subjectAltName=DNS:$hostname" \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
  -addext 'extendedKeyUsage=serverAuth'
openssl pkcs12 -export \
  -out "$certificate_dir/appgw.pfx" \
  -inkey "$certificate_dir/appgw.key" \
  -in "$certificate_dir/appgw.crt" \
  -name sre-lab-tls \
  -passout "file:$certificate_dir/pfx-password"
openssl verify -CAfile "$certificate_dir/appgw.crt" \
  -verify_hostname "$hostname" "$certificate_dir/appgw.crt"
printf 'Created a 90-day lab certificate in the ignored .certs directory.\n'