set -e
cd /out
# openresty:alpine ships openssl libs but not the CLI; install it in this throwaway container
command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl
# 1) local root CA (10y)
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -subj "/C=CN/O=Zerocode/CN=Zerocode Local Root CA" -out ca.crt
# 2) wildcard key + CSR (CN=*.zerocode.com)
openssl genrsa -out wildcard.zerocode.com.key 2048
openssl req -new -key wildcard.zerocode.com.key -subj "/C=CN/O=Zerocode/CN=*.zerocode.com" -out wildcard.csr
# 3) SAN: *.zerocode.com (one-level wildcard) + zerocode.com (apex)
printf 'subjectAltName = DNS:*.zerocode.com, DNS:zerocode.com\n' > san.ext
# 4) CA signs the wildcard cert (825 days)
openssl x509 -req -in wildcard.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out wildcard.zerocode.com.crt -days 825 -sha256 -extfile san.ext
chmod 644 ca.crt wildcard.zerocode.com.crt
chmod 600 ca.key wildcard.zerocode.com.key
rm -f wildcard.csr san.ext ca.srl
echo "---- generated in /out ----"
ls -1
