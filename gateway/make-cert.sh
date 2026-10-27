set -e
cd /out
# openresty:alpine ships openssl libs but not the CLI; install it in this throwaway container
command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl

# Domain comes from the caller (make-cert.ps1 passes it as $1) or from GATEWAY_DOMAIN.
# Output names are deliberately domain-independent: nginx.conf points at gateway.crt/gateway.key
# and `acme.sh --install-cert` writes that same pair, so switching between the local self-signed
# cert and a real Let's Encrypt wildcard never requires editing nginx.conf.
DOMAIN="${1:-${GATEWAY_DOMAIN:-zerocode.com}}"
DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
case "$DOMAIN" in .*) DOMAIN="${DOMAIN#.}" ;; esac   # tolerate nginx-style leading dot

echo ">> self-signing *.${DOMAIN} -> gateway.crt / gateway.key"
# 1) local root CA (10y)
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -subj "/C=CN/O=Zerocode/CN=Zerocode Local Root CA" -out ca.crt
# 2) wildcard key + CSR (CN=*.<domain>)
openssl genrsa -out gateway.key 2048
openssl req -new -key gateway.key -subj "/C=CN/O=Zerocode/CN=*.${DOMAIN}" -out wildcard.csr
# 3) SAN: *.<domain> (one-level wildcard) + <domain> (apex)
printf 'subjectAltName = DNS:*.%s, DNS:%s\n' "$DOMAIN" "$DOMAIN" > san.ext
# 4) CA signs the wildcard cert. 825 days is not arbitrary: macOS/iOS reject any TLS cert
#    valid longer than 825 days, even one issued by a CA the user installed themselves.
openssl x509 -req -in wildcard.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out gateway.crt -days 825 -sha256 -extfile san.ext
chmod 644 ca.crt gateway.crt
chmod 600 ca.key gateway.key
rm -f wildcard.csr san.ext ca.srl
echo "---- generated in /out ----"
ls -1
