#!/usr/bin/env bash
# Retrato do ambiente Linux do servidor (requisito 5.7: configuração e monitoramento).
# Mostra sistema, interfaces, rotas, o serviço no systemd, portas e conexões,
# recursos do processo (ps e top), firewall, contêineres das unidades, o painel
# HTTP, o log do serviço, permissões e disco.
#
# Uso: sudo bash scripts/diagnostico.sh [ip_para_ping] [pasta_de_dados]
#      sudo bash scripts/diagnostico.sh 172.28.0.21 > docs/evidencias/11_diagnostico_final.txt

set -u
ALVO="${1:-}"
DADOS="${2:-/srv/laudos}"
PORTA="${LTP_PORTA:-5050}"
PORTA_HTTP="${LTP_PORTA_HTTP:-8080}"

secao() { printf '\n===== %s =====\n' "$1"; }

secao "Host e sistema"
date '+%Y-%m-%d %H:%M:%S %Z'
hostname
grep PRETTY_NAME /etc/os-release
uname -r
uptime

secao "Interfaces (ip -br addr) e rotas (ip route)"
ip -br addr
ip route

secao "Serviço no systemd (systemctl)"
systemctl is-enabled laudos 2>/dev/null && systemctl --no-pager status laudos | head -n 11

secao "Portas em escuta: LTP/1 $PORTA e HTTP $PORTA_HTTP (ss -tlnp)"
ss -tlnp "( sport = :$PORTA or sport = :$PORTA_HTTP )"

secao "Conexões TCP estabelecidas na porta $PORTA (ss -tn)"
ss -tn state established "( sport = :$PORTA )"

secao "Processo do servidor: usuário, threads, memória, CPU (ps e top)"
PID="$(systemctl show -p MainPID --value laudos 2>/dev/null)"
[ -n "$PID" ] && [ "$PID" != 0 ] || PID="$(pgrep -f '[s]rc/servidor.py' | head -n 1)"
if [ -n "$PID" ]; then
    ps -o pid,user,group,nlwp,rss,%cpu,%mem,etime,cmd -p "$PID"
    top -b -n 1 -p "$PID" | head -n 8
else
    echo "servidor não está rodando"
fi

secao "Firewall (ufw status verbose)"
ufw status verbose 2>/dev/null || echo "ufw indisponível"

secao "Unidades em contêineres (docker compose ps)"
if command -v docker >/dev/null; then
    docker compose -f "$(dirname "$0")/../docker-compose.yml" ps \
        --format 'table {{.Name}}\t{{.Status}}' 2>/dev/null
    docker network inspect laudos_rede_clinica \
        --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}' 2>/dev/null
fi

secao "Painel HTTP (curl /saude)"
curl -s -m 3 "http://127.0.0.1:$PORTA_HTTP/saude" || echo "painel sem resposta"

if [ -n "$ALVO" ]; then
    secao "Alcance de $ALVO (ping)"
    ping -c 3 -W 2 "$ALVO" || echo "sem resposta ao ping"
fi

secao "Últimas linhas do log do serviço (journalctl -u laudos)"
journalctl -u laudos -n 12 --no-pager -o short-iso 2>/dev/null || tail -n 12 /var/log/laudos/servidor.log

secao "Permissões: configuração (/etc/laudos) e laudos ($DADOS)"
ls -ld /opt/laudos/src /etc/laudos /etc/laudos/tls "$DADOS" 2>/dev/null
ls -l /etc/laudos/tls 2>/dev/null
for unidade in "$DADOS"/*/; do
    unidade="${unidade%/}"
    printf '%s: ' "$(basename "$unidade")"
    stat -c '%A %U:%G' "$unidade/entrada" | tr '\n' ' '
    echo "| $(find "$unidade/entrada" -type f ! -name '*.sha256' | wc -l) laudos," \
         "$(find "$unidade/.parciais" -type f | wc -l) parciais"
done
f="$(find "$DADOS" -path '*/entrada/*' -type f ! -name '*.sha256' | head -n 1)"
[ -n "$f" ] && ls -l "$f" "$f.sha256"

secao "Uso de disco (df -h)"
df -h "$DADOS"
