#!/usr/bin/env bash
# Captura do tráfego LTP/1 com tcpdump, sem e com TLS (requisitos 5.7 e 5.9).
#
#  1. sem TLS: sobe uma instância temporária do servidor em 127.0.0.1:5051,
#     captura na interface lo e envia um laudo de texto da unidade centro;
#  2. com TLS: captura na interface br-clinica (rede das unidades) enquanto o
#     contêiner centro envia o mesmo laudo ao serviço real (porta 5050).
#
# As capturas (.pcap) ficam em docs/evidencias/capturas/ e abrem no Wireshark
# (na de TLS, use "Decode As... TCP 5050 -> TLS" para ver o handshake).
#
# Uso: sudo bash scripts/captura_trafego.sh | tee docs/evidencias/09_captura_trafego.txt
#      sudo SAIDA=/tmp/captura bash scripts/captura_trafego.sh   (sem tocar nas evidências)

set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "execute com sudo"; exit 1; }
cd "$(dirname "$0")/.."
SAIDA="${SAIDA:-docs/evidencias/capturas}"
TMP="$(mktemp -d)"
mkdir -p "$SAIDA"
LAUDO="laudo-exemplo-$(date +%H%M%S).txt"
# Texto sem acentos: o tcpdump -A mostra bytes fora do ASCII como "."
printf 'LAUDO DE EXEMPLO (dados ficticios)\nPaciente: Maria Exemplo\nExame: tomografia computadorizada\nConclusao: sem alteracoes\n' > "$TMP/$LAUDO"
secao() { printf '\n===== %s =====\n' "$1"; }

secao "1. Sem TLS (instância temporária em 127.0.0.1:5051, captura em lo)"
python3 src/servidor.py --host 127.0.0.1 --porta 5051 --http-porta 0 \
    --dados "$TMP/dados" --log "" > "$TMP/servidor.txt" 2>&1 &
SRV=$!
tcpdump -i lo -nn -s0 -U -w "$SAIDA/ltp_sem_tls.pcap" 'tcp port 5051' 2>/dev/null &
CAP=$!
sleep 1.5
LTP_TOKEN=token-centro-2026 python3 src/cliente.py --servidor 127.0.0.1 --porta 5051 \
    --unidade centro enviar "$TMP/$LAUDO" --destino batel
sleep 1
kill "$CAP" "$SRV"; wait "$CAP" "$SRV" 2>/dev/null
echo "--- pacotes capturados (tcpdump -nn -r, primeiras linhas)"
tcpdump -nn -r "$SAIDA/ltp_sem_tls.pcap" 2>/dev/null | head -n 8
echo "--- o que qualquer um na rede consegue ler (tcpdump -A, só texto)"
tcpdump -nn -A -r "$SAIDA/ltp_sem_tls.pcap" 2>/dev/null \
    | grep -aoE '\{"cmd"[^}]*\}|Paciente: [A-Za-z ]+|Exame: [a-z ]+' | head -n 6

secao "2. Com TLS (serviço real em 5050, captura em br-clinica)"
docker compose cp "$TMP/$LAUDO" "centro:/home/laudos/$LAUDO" >/dev/null 2>&1
docker compose exec -T -u root centro chown laudos "/home/laudos/$LAUDO"
tcpdump -i br-clinica -nn -s0 -U -w "$SAIDA/ltp_com_tls.pcap" 'tcp port 5050' 2>/dev/null &
CAP=$!
sleep 1.5
docker compose exec -T centro python src/cliente.py enviar "/home/laudos/$LAUDO" --destino batel
sleep 1
kill "$CAP"; wait "$CAP" 2>/dev/null
echo "--- pacotes capturados (tcpdump -nn -r, primeiras linhas)"
tcpdump -nn -r "$SAIDA/ltp_com_tls.pcap" 2>/dev/null | head -n 8
echo "--- procurando o token e o nome da paciente no tráfego cifrado"
for termo in token-centro Paciente cmd; do
    n=$(tcpdump -nn -A -r "$SAIDA/ltp_com_tls.pcap" 2>/dev/null | grep -ac "$termo")
    echo "\"$termo\": $n ocorrência(s)"
done
chmod 644 "$SAIDA"/*.pcap
rm -rf "$TMP"
