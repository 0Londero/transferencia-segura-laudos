#!/usr/bin/env bash
# Testes na topologia final (3 unidades em contêineres + servidor no host Linux).
#
#   T15  rede: IPs, rotas, nomes (DNS do Docker) e alcance do servidor
#   T16  as 3 unidades (3 contêineres, 3 IPs) enviando ao mesmo tempo, com link
#        limitado a 4 MB/s, enquanto o host observa conexões, threads e métricas
#   T17  firewall: unidade alcança 5050/TCP, mas não o painel HTTP (8080/TCP)
#   T18  unidade de destino baixa o laudo e confere o SHA-256
#
# Uso, no Linux do host, a partir da pasta do projeto (root lê o journal do
# kernel e a pasta /srv/laudos):
#   sudo bash scripts/teste_conteineres.sh | tee docs/evidencias/07_testes_conteineres.txt

set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "execute com sudo"; exit 1; }
cd "$(dirname "$0")/.."
SUFIXO="$(date +%H%M%S)"
dc() { docker compose exec -T "$@"; }
secao() { printf '\n===== %s =====\n' "$1"; }

secao "Contêineres e rede (docker compose ps, docker network inspect)"
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
docker network inspect laudos_rede_clinica \
    --format '{{range .IPAM.Config}}rede {{.Subnet}}, gateway {{.Gateway}}{{end}}'
docker network inspect laudos_rede_clinica \
    --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'
ip -br addr show br-clinica

secao "T15: visão de rede da unidade centro"
dc centro sh -c 'hostname; ip -br addr show eth0; ip route; grep servidor /etc/hosts'
echo "--- nomes das outras unidades pelo DNS interno do Docker (127.0.0.11)"
dc centro sh -c 'for u in batel agua-verde; do printf "%s -> " $u; dig +short $u; done'
echo "--- ping até o servidor"
dc centro ping -c 3 servidor
echo "--- caminho até a internet: unidade -> host Linux (172.28.0.1) -> Windows (NAT do WSL)"
dc centro traceroute -n -q 1 -m 3 8.8.8.8
echo "--- PING do LTP/1 com TLS"
dc centro python src/cliente.py ping

secao "T16: 3 unidades enviando 20 MB ao mesmo tempo (link limitado a 4 MB/s)"
for u in centro batel agua-verde; do
    dc "$u" sh -c "head -c 20M /dev/urandom > /home/laudos/laudo-$u-$SUFIXO.pdf"
done
inicio=$(date +%s.%N)
dc centro python src/cliente.py enviar "/home/laudos/laudo-centro-$SUFIXO.pdf" \
    --destino batel --limite 4096 > /tmp/t16-centro.txt 2>&1 &
dc batel python src/cliente.py enviar "/home/laudos/laudo-batel-$SUFIXO.pdf" \
    --destino agua-verde --limite 4096 > /tmp/t16-batel.txt 2>&1 &
dc agua-verde python src/cliente.py enviar "/home/laudos/laudo-agua-verde-$SUFIXO.pdf" \
    --destino centro --limite 4096 > /tmp/t16-agua-verde.txt 2>&1 &
sleep 2.5
echo "--- no host, durante os envios: conexões na porta 5050 (ss -tn)"
ss -tn state established '( sport = :5050 )'
echo "--- processo do servidor: threads (NLWP), memória (RSS, KB) e CPU (ps)"
ps -o pid,user,nlwp,rss,%cpu,etime,cmd -p "$(systemctl show -p MainPID --value laudos)"
echo "--- métricas do painel HTTP no mesmo instante"
curl -s http://127.0.0.1:8080/metricas | python3 -c '
import json, sys
m = json.load(sys.stdin)
print("conexoes_ativas:", m["contadores"]["conexoes_ativas"], "| threads:",
      m["processo"]["threads"], "| RAM:", m["processo"]["memoria_rss_kb"] // 1024, "MB")'
wait
fim=$(date +%s.%N)
for u in centro batel agua-verde; do echo "--- saída do cliente $u"; cat "/tmp/t16-$u.txt"; done
awk -v a="$inicio" -v b="$fim" \
    'BEGIN { printf "tempo total: %.1f s (cada envio sozinho levaria 5 s a 4 MB/s)\n", b - a }'

secao "T17: firewall visto da unidade"
printf '5050/TCP (LTP/1): '
dc centro python -c "import socket; socket.create_connection(('servidor', 5050), timeout=3); print('aberta')"
printf '8080/TCP (painel HTTP, só para a TI): '
if codigo=$(dc centro curl -s -m 3 -o /dev/null -w '%{http_code}' http://servidor:8080/saude); then
    echo "respondeu HTTP $codigo (o firewall deveria bloquear)"
else
    echo "sem resposta em 3 s (bloqueada pelo ufw)"
fi
echo "--- registro do bloqueio no log do kernel (journalctl -k)"
journalctl -k --since "-1min" --no-pager | grep "UFW BLOCK" | grep "DPT=8080" | tail -n 2

secao "T18: batel baixa o laudo enviado por centro e confere o SHA-256"
dc batel python src/cliente.py listar
dc batel python src/cliente.py baixar "laudo-centro-$SUFIXO.pdf"
dc batel sh -c "cd /home/laudos/recebidos && sha256sum -c laudo-centro-$SUFIXO.pdf.sha256"
echo "--- o mesmo hash conferido no servidor (sha256sum -c na pasta do serviço)"
(cd /srv/laudos/batel/entrada && sha256sum -c "laudo-centro-$SUFIXO.pdf.sha256")
