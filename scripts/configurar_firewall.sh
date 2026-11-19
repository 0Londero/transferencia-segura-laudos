#!/usr/bin/env bash
# Firewall do servidor com ufw (requisito 5.9):
#
#   entrada bloqueada por padrão (deny) e saída liberada;
#   5050/TCP (LTP/1)        liberada para a rede das unidades e para a rede da TI;
#   8080/TCP (painel HTTP)  liberada SÓ para a rede da TI.
#
# Rede das unidades: a rede Docker "rede_clinica" (172.28.0.0/24).
# Rede da TI: a sub-rede da eth0; no WSL 2 é a rede NAT que liga o Linux ao
# notebook Windows. O WSL pode trocar essa rede ao reiniciar: nesse caso,
# execute o script de novo.
#
# Uso: sudo bash scripts/configurar_firewall.sh

set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "execute com sudo"; exit 1; }
REDE_UNIDADES="${REDE_UNIDADES:-172.28.0.0/24}"
REDE_TI="${REDE_TI:-$(ip route show dev eth0 scope link | awk '{print $1; exit}')}"
[ -n "$REDE_TI" ] || { echo "não achei a rede da eth0; informe REDE_TI=a.b.c.d/m"; exit 1; }
echo "rede das unidades: $REDE_UNIDADES | rede da TI: $REDE_TI"

ufw default deny incoming
ufw default allow outgoing
ufw allow from "$REDE_UNIDADES" to any port 5050 proto tcp comment 'LTP/1 unidades'
ufw allow from "$REDE_TI" to any port 5050 proto tcp comment 'LTP/1 TI'
ufw allow from "$REDE_TI" to any port 8080 proto tcp comment 'painel HTTP TI'
ufw logging low          # pacotes bloqueados aparecem no log do kernel como [UFW BLOCK]
ufw --force enable
ufw status verbose
