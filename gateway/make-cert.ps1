# gateway/make-cert.ps1
# Self-signed wildcard cert generator (local Windows + Docker).
# Creates a local root CA and signs a *.zerocode.com wildcard cert into gateway/certs/ (gitignored).
# The real work lives in gateway/make-cert.sh; we run it with openssl from the openresty image.
#
# Usage:  powershell -ExecutionPolicy Bypass -File gateway/make-cert.ps1
# Then import gateway/certs/ca.crt into "Trusted Root Certification Authorities" to trust *.zerocode.com.
# NOTE: keep this file ASCII-only so Windows PowerShell 5.1 (ANSI/GBK default) tokenizes it correctly.

$ErrorActionPreference = "Stop"

$root  = $PSScriptRoot
$certs = Join-Path $root "certs"
$image = if ($env:OPENRESTY_IMAGE) { $env:OPENRESTY_IMAGE } else { "openresty/openresty:alpine" }

New-Item -ItemType Directory -Force -Path $certs | Out-Null

# Docker Desktop dislikes backslashes in -v paths; normalize to forward slashes
$certsPosix   = ($certs -replace '\\', '/')
$scriptPosix  = (($root + '/make-cert.sh') -replace '\\', '/')

Write-Host ">> generating self-signed CA + *.zerocode.com cert via openssl in $image"
# tr -d '\r' guards against CRLF-authored make-cert.sh handing busybox sh stray \r tokens
docker run --rm -v "${certsPosix}:/out" -v "${scriptPosix}:/mk.sh" $image sh -c "tr -d '\r' < /mk.sh > /tmp/mk.sh; sh /tmp/mk.sh"

if ($LASTEXITCODE -ne 0) { throw "openssl generation failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "certs written to $certs"
Write-Host "next steps:"
Write-Host "  1) import ca.crt into 'Trusted Root Certification Authorities' (local demo only)"
Write-Host "  2) add a hosts line for each app you want to reach:  127.0.0.1  <slug>.zerocode.com"
Write-Host "  3) start the gateway:  docker compose -f gateway/docker-compose.yml up -d"
