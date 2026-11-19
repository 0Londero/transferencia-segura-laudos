#!/usr/bin/env bash
# Demonstração de concorrência para o vídeo: as 3 unidades (3 contêineres, 3 IPs)
# enviam um laudo de 60 MB ao mesmo tempo, cada uma com o link limitado a
# 4 MB/s (cerca de 15 s). Enquanto roda, acompanhe o servidor com
# scripts/monitorar.sh e o painel HTTP.
#
# Uso: bash scripts/demo_tres_unidades.sh   (usuário no grupo docker)

set -uo pipefail
cd "$(dirname "$0")/.."
SUFIXO="$(date +%H%M%S)"
ROTAS=("centro batel" "batel agua-verde" "agua-verde centro")

for rota in "${ROTAS[@]}"; do
    read -r origem _ <<< "$rota"
    docker compose exec -T "$origem" \
        sh -c "head -c 60M /dev/urandom > /home/laudos/exame-$origem-$SUFIXO.pdf"
done

echo "== $(date +%H:%M:%S) 3 envios simultâneos de 60 MB, a 4 MB/s por unidade"
for rota in "${ROTAS[@]}"; do
    read -r origem destino <<< "$rota"
    docker compose exec -T "$origem" python src/cliente.py enviar \
        "/home/laudos/exame-$origem-$SUFIXO.pdf" --destino "$destino" --limite 4096 2>&1 \
        | sed -u "s/^/[$origem] /" &
done
wait
echo "== $(date +%H:%M:%S) fim: os 3 laudos chegaram juntos"
