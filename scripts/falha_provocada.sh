#!/usr/bin/env bash
# T19, falha provocada: o serviço cai no meio de um envio e volta depois.
# O cliente trata o erro, tenta reconectar com espera exponencial e, quando o
# servidor volta, retoma o envio do byte em que parou; o SHA-256 confere.
#
# Uso: sudo bash scripts/falha_provocada.sh | tee docs/evidencias/10_falha_provocada.txt

set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "execute com sudo"; exit 1; }
cd "$(dirname "$0")/.."
LAUDO="laudo-grande-$(date +%H%M%S).pdf"
INICIO="$(date '+%Y-%m-%d %H:%M:%S')"
secao() { printf '\n===== %s %s =====\n' "$(date +%H:%M:%S)" "$1"; }

docker compose exec -T batel sh -c "head -c 30M /dev/urandom > /home/laudos/$LAUDO"

secao "batel envia 30 MB para centro, com o link limitado a 3 MB/s (cerca de 10 s)"
docker compose exec -T batel python src/cliente.py enviar "/home/laudos/$LAUDO" \
    --destino centro --limite 3072 2>&1 &
CLIENTE=$!
sleep 4

secao "FALHA PROVOCADA: systemctl stop laudos"
systemctl stop laudos
echo "estado do serviço: $(systemctl is-active laudos)"
echo "porta 5050 em escuta: $(ss -tln '( sport = :5050 )' | tail -n +2 | wc -l) socket(s)"
echo "envio parcial guardado no servidor:"
ls -l /srv/laudos/centro/.parciais/
sleep 6

secao "SOLUÇÃO: systemctl start laudos"
systemctl start laudos
echo "estado do serviço: $(systemctl is-active laudos)"
wait "$CLIENTE"

secao "conferência no servidor (sha256sum -c)"
(cd /srv/laudos/centro/entrada && sha256sum -c "$LAUDO.sha256")

secao "o mesmo episódio visto pelo journal do serviço (journalctl -u laudos)"
journalctl -u laudos --since "$INICIO" --no-pager -o short-iso \
    | grep -vE 'conexão (aceita|encerrada)|canal TLS|autenticada'
