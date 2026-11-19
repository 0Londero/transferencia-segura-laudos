#!/usr/bin/env bash
# Monitor ao vivo do servidor (requisito 5.7): a cada segundo mostra as
# conexões estabelecidas na porta 5050 (ss) e as threads, a memória e a CPU
# do processo do serviço (ps). Ctrl+C para sair.
#
# Uso: bash scripts/monitorar.sh

PORTA="${LTP_PORTA:-5050}"
while true; do
    PID="$(systemctl show -p MainPID --value laudos 2>/dev/null)"
    [ -n "$PID" ] && [ "$PID" != 0 ] || PID="$(pgrep -f '[s]rc/servidor.py' | head -n 1)"
    clear
    echo "== $(date +%H:%M:%S) conexões estabelecidas na porta $PORTA (ss -tn)"
    ss -tn state established "( sport = :$PORTA )"
    echo
    echo "== processo do servidor (ps): threads (NLWP), memória (RSS, KB) e CPU"
    if [ -n "$PID" ]; then
        ps -o pid,user,nlwp,rss,%cpu,etime -p "$PID"
    else
        echo "servidor parado"
    fi
    sleep 1
done
