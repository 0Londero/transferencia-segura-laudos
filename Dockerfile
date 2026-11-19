# Imagem das unidades da clínica (clientes LTP/1). Cada unidade é um contêiner
# com IP próprio na rede "rede_clinica"; o servidor roda no Linux do host,
# como serviço do systemd (scripts/instalar_servidor.sh).
# A aplicação usa só a biblioteca padrão do Python (3.11 ou mais novo).
FROM python:3.12-slim

# Ferramentas de diagnóstico de rede usadas no monitoramento:
# ip, ss e tc (iproute2), ping, ps, curl, traceroute e dig
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 iputils-ping procps curl traceroute dnsutils \
    && rm -rf /var/lib/apt/lists/*

# Mínimo privilégio: o cliente roda com um usuário comum, sem root
RUN useradd --uid 10001 --create-home --home-dir /home/laudos \
        --shell /usr/sbin/nologin laudos

WORKDIR /app
COPY src/ ./src/
COPY testes/ ./testes/

USER laudos
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# O contêiner fica no ar; os comandos do cliente rodam com "docker compose exec"
CMD ["sleep", "infinity"]
