#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the local/VM nginx deployment.
# Usage: deploy/generate-cert.sh  (or: make certs)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/certs"

openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout "$DIR/certs/server.key" \
  -out "$DIR/certs/server.crt" \
  -subj "/CN=pharmacy.local" \
  -addext "subjectAltName=DNS:pharmacy.local,DNS:localhost,IP:127.0.0.1"

chmod 600 "$DIR/certs/server.key"
echo "Certificate written to deploy/certs/ (server.crt + server.key)"
