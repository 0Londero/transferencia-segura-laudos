#!/usr/bin/env bash
# Instala a central de laudos como serviço do systemd (requisito 5.7).
#
#   usuário "laudos"   conta de sistema, sem shell e sem senha (mínimo privilégio)
#   /opt/laudos/src    código, dono root: o serviço não consegue alterar o próprio código
#   /etc/laudos        tokens das unidades e chave TLS, legíveis só pelo grupo laudos
#   /srv/laudos        laudos (750, dono laudos)
#   logs               journal (journalctl -u laudos) e /var/log/laudos/servidor.log
#
# Pode ser executado de novo para atualizar o código (o certificado é mantido).
# Uso, a partir da pasta do projeto: sudo bash scripts/instalar_servidor.sh

set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "execute com sudo"; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN="${SUDO_USER:-}"

echo "== 1. usuário de serviço"
if ! id laudos >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /srv/laudos --no-create-home \
        --shell /usr/sbin/nologin laudos
fi
# O administrador entra no grupo laudos: lê laudos e logs sem precisar de root
if [ -n "$ADMIN" ] && [ "$ADMIN" != root ]; then
    usermod -aG laudos "$ADMIN"
fi
id laudos
getent passwd laudos

echo "== 2. código em /opt/laudos"
install -d -o root -g root -m 755 /opt/laudos /opt/laudos/src
install -o root -g root -m 644 "$REPO"/src/*.py /opt/laudos/src/

echo "== 3. configuração e certificados em /etc/laudos"
install -d -o root -g laudos -m 750 /etc/laudos
install -o root -g laudos -m 640 "$REPO/config/unidades.json" /etc/laudos/unidades.json
if [ ! -f /etc/laudos/tls/servidor.crt ]; then
    bash "$REPO/scripts/gerar_certificados.sh" /etc/laudos/tls
fi
chown -R root:laudos /etc/laudos/tls
chmod 750 /etc/laudos/tls
chmod 640 /etc/laudos/tls/servidor.key   # o serviço lê pelo grupo
chmod 600 /etc/laudos/tls/ca.key         # a chave da CA só o root lê
# Os clientes só precisam do certificado público da CA
install -d "$REPO/certs"
cp /etc/laudos/tls/ca.crt "$REPO/certs/ca.crt"

echo "== 4. pasta dos laudos"
install -d -o laudos -g laudos -m 750 /srv/laudos

echo "== 5. serviço do systemd"
install -o root -g root -m 644 "$REPO/deploy/laudos.service" /etc/systemd/system/laudos.service
systemctl daemon-reload
systemctl enable laudos
systemctl restart laudos
sleep 2
systemctl --no-pager status laudos | head -n 12

echo "== 6. permissões resultantes"
ls -ld /opt/laudos/src /etc/laudos /etc/laudos/tls /srv/laudos
ls -l /etc/laudos /etc/laudos/tls
