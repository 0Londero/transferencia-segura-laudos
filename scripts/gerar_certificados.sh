#!/usr/bin/env bash
# Gera a CA da clínica e o certificado TLS do servidor LTP/1 (requisito 5.9).
#
#   ca.key / ca.crt            autoridade certificadora da clínica (a chave fica só no servidor)
#   servidor.key / servidor.crt certificado do servidor, assinado pela CA
#
# Os clientes recebem só o ca.crt e conferem se o servidor apresenta um
# certificado assinado por essa CA com o nome "servidor" (resistente a um
# servidor falso na rede). Curva elíptica P-256: chave curta e handshake rápido.
#
# Uso: bash scripts/gerar_certificados.sh <pasta_de_saida> [IP_extra ...]
#      (o instalar_servidor.sh chama este script com /etc/laudos/tls)

set -euo pipefail
SAIDA="${1:?informe a pasta de saída}"
shift
SAN="DNS:servidor,DNS:localhost,IP:127.0.0.1,IP:172.28.0.1"
for ip in "$@"; do SAN="$SAN,IP:$ip"; done

mkdir -p "$SAIDA"
cd "$SAIDA"
umask 077   # chaves privadas nascem legíveis só pelo dono

if [ ! -f ca.key ]; then
    cat > ca.cnf <<'EOF'
[req]
prompt = no
distinguished_name = dn
x509_extensions = v3_ca
[dn]
O = Clinica PSI129A (laboratorio)
CN = CA da clinica
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
EOF
    openssl req -x509 -new -config ca.cnf -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
        -nodes -keyout ca.key -out ca.crt -days 825
    rm -f ca.cnf
    echo "CA criada: $SAIDA/ca.crt"
fi

cat > servidor.cnf <<EOF
[req]
prompt = no
distinguished_name = dn
[dn]
O = Clinica PSI129A (laboratorio)
CN = servidor
[ext]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = $SAN
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF
openssl req -new -config servidor.cnf -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -nodes -keyout servidor.key -out servidor.csr
openssl x509 -req -in servidor.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -extfile servidor.cnf -extensions ext -days 397 -out servidor.crt
rm -f servidor.csr servidor.cnf ca.srl
chmod 644 ca.crt servidor.crt
chmod 600 ca.key servidor.key

openssl x509 -in servidor.crt -noout -subject -issuer -dates -ext subjectAltName
