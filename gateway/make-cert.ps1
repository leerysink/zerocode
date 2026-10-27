# gateway/make-cert.ps1
# Self-signed wildcard cert generator (local Windows + Docker).
# Creates a local root CA and signs a *.<domain> wildcard cert into gateway/certs/ (gitignored).
# The real work lives in gateway/make-cert.sh; we run it with openssl from the openresty image.
#
# Usage:  powershell -ExecutionPolicy Bypass -File gateway/make-cert.ps1 [-Domain example.com]
# Then import gateway/certs/ca.crt into "Trusted Root Certification Authorities" to trust *.<domain>.
# NOTE: keep this file ASCII-only so Windows PowerShell 5.1 (ANSI/GBK default) tokenizes it correctly.

# param must stay the first statement of the script. Its default reads the very same env var the
# platform backend uses (app/deploy/nics.py), so the cert you sign cannot drift from the domain
# the platform is advertising.
param(
    [string]$Domain = $(if ($env:ZEROCODE_GATEWAY_DOMAIN) { $env:ZEROCODE_GATEWAY_DOMAIN } else { "zerocode.com" })
)

$ErrorActionPreference = "Stop"

# Fail closed on anything that is not a plain hostname: a bogus domain produces a cert that
# silently matches nothing, and a shell metacharacter would reach the `sh -c` string below.
if ($Domain -notmatch '^[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?$') {
    throw "invalid -Domain '$Domain' (expected a hostname such as example.com)"
}
$Domain = $Domain.ToLowerInvariant().TrimStart('.')

$root  = $PSScriptRoot
$certs = Join-Path $root "certs"
$image = if ($env:OPENRESTY_IMAGE) { $env:OPENRESTY_IMAGE } else { "openresty/openresty:alpine" }

New-Item -ItemType Directory -Force -Path $certs | Out-Null

# Docker Desktop dislikes backslashes in -v paths; normalize to forward slashes
$certsPosix   = ($certs -replace '\\', '/')
$scriptPosix  = (($root + '/make-cert.sh') -replace '\\', '/')

Write-Host ">> generating self-signed CA + *.$Domain cert via openssl in $image"
# tr -d '\r' guards against CRLF-authored make-cert.sh handing busybox sh stray \r tokens.
# The domain travels as a single-quoted argv element instead of being spliced bare into the shell
# string, so make-cert.sh receives it as $1 and nothing inside it can be re-interpreted by sh.
docker run --rm -v "${certsPosix}:/out" -v "${scriptPosix}:/mk.sh" $image sh -c "tr -d '\r' < /mk.sh > /tmp/mk.sh; sh /tmp/mk.sh '$Domain'"

if ($LASTEXITCODE -ne 0) { throw "openssl generation failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "certs written to $certs"
Write-Host "next steps:"
Write-Host "  1) import ca.crt into 'Trusted Root Certification Authorities' (local demo only)"
Write-Host "  2) set ZEROCODE_GATEWAY_DOMAIN=$Domain in BOTH gateway/.env and the platform .env"
Write-Host "  3) local demo only: add a hosts line per app  127.0.0.1  <slug>.$Domain"
Write-Host "     (skip on a server where real DNS already resolves *.$Domain to this host)"
Write-Host "  4) start the gateway:  docker compose -f gateway/docker-compose.yml up -d"
Write-Host ""
Write-Host "Self-signed is for local demo only. For a real domain use acme.sh + Let's Encrypt over"
Write-Host "DNS-01; its --install-cert writes the same gateway.crt/gateway.key pair nginx.conf already"
Write-Host "points at, so switching from this cert to a public one needs no nginx config change."

# nginx.conf reads fixed names (gateway.crt/key). Files from the older domain-named scheme are
# no longer referenced by anything; flag them instead of deleting, since certs/ is user data.
$orphans = @(Get-ChildItem -Path $certs -Filter 'wildcard.*' -File -ErrorAction SilentlyContinue)
if ($orphans.Count -gt 0) {
    Write-Host ""
    Write-Host "NOTE: these old domain-named files are unused now (nginx.conf reads gateway.crt/key):"
    $orphans | ForEach-Object { Write-Host "  - $($_.Name)" }
}
