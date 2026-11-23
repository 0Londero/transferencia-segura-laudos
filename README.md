<p align="center">
  <img src="docs/img/pucpr.png" alt="Logo da PUCPR" width="160">
</p>

# Transferência segura de laudos (LTP/1)

Projeto Final de **PSI129A, Conectividade em Sistemas Ciberfísicos** (PUCPR, 2026/2).
Tema 4 do enunciado: transferência segura de arquivos.

**Equipe:** Otávio Londero, Davi Berbek e Pedro Joaquim Freire de Lima.

**Vídeo de apresentação:** https://youtu.be/XPR5XRYpV-k

## Cenário

Uma clínica de diagnóstico por imagem com três unidades em Curitiba (Centro, Batel e
Água Verde) troca laudos entre as unidades todos os dias. A solução substitui o envio
por e-mail e pen drive por uma central de laudos em Linux que:

- recebe os laudos por **TCP com TLS 1.3**, com **token** por unidade;
- confere o **SHA-256** de cada laudo e descarta o que chegar diferente;
- **retoma** envios e downloads interrompidos a partir do último byte gravado;
- guarda os laudos em **diretórios por unidade**: cada unidade só lê a própria entrada;
- atende **várias unidades ao mesmo tempo** (uma thread por conexão, limite de 20);
- publica um **painel HTTP** (`/`, `/saude`, `/metricas`) para a TI da clínica;
- roda como **serviço do systemd**, com usuário próprio, firewall (`ufw`) e logs no journal.

## Arquitetura

```
 Notebook da TI (Windows 11)                  Servidor central (Ubuntu 26.04 no WSL 2)
 172.19.112.1 (rede NAT do WSL)               eth0 172.19.114.51/20
 navegador -> painel HTTP :8080  ===========> laudos.service (systemd, usuário laudos)
 testes -> LTP/1 com TLS :5050                  LTP/1 com TLS :5050 | painel HTTP :8080
                                                ufw: 5050 unidades+TI, 8080 só TI
                                                /srv/laudos/<unidade>/entrada
                                              br-clinica 172.28.0.1/24 (gateway)
                                                 |
                       rede_clinica 172.28.0.0/24 (Docker bridge)
                 +---------------+---------------+-----------------+
                 | centro        | batel         | agua-verde      |
                 | 172.28.0.21   | 172.28.0.22   | 172.28.0.23     |
                 +---------------+---------------+-----------------+
                      unidades: clientes LTP/1 em contêineres
```

| Nó | Função | IP/máscara | Gateway | Portas |
|---|---|---|---|---|
| Host Windows 11 | TI da clínica (painel, testes) | 172.19.112.1/20 | rede doméstica | efêmeras |
| Ubuntu 26.04 no WSL 2 (`eth0`) | servidor central | 172.19.114.51/20 | 172.19.112.1 | 5050/TCP, 8080/TCP |
| Ubuntu (`br-clinica`) | gateway da rede das unidades | 172.28.0.1/24 | não se aplica | 5050/TCP |
| `centro` | unidade Centro | 172.28.0.21/24 | 172.28.0.1 | efêmera |
| `batel` | unidade Batel | 172.28.0.22/24 | 172.28.0.1 | efêmera |
| `agua-verde` | unidade Água Verde | 172.28.0.23/24 | 172.28.0.1 | efêmera |

O endereço da `eth0` do WSL é dado pelo Windows e pode mudar ao reiniciar: confira
com `ip -br addr show eth0`.

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `src/protocolo.py` | formato das mensagens LTP/1, validações e contextos TLS |
| `src/servidor.py` | servidor TCP multithread (UPLOAD, DOWNLOAD, LIST, retomada, SHA-256) |
| `src/monitoramento.py` | métricas e painel HTTP (`/`, `/saude`, `/metricas`) |
| `src/cliente.py` | cliente de linha de comando das unidades |
| `config/unidades.json` | unidades e SHA-256 dos tokens |
| `deploy/laudos.service` | unidade do systemd, com endurecimento e limites |
| `scripts/instalar_servidor.sh` | cria o usuário, as pastas, os certificados e o serviço |
| `scripts/gerar_certificados.sh` | CA da clínica e certificado do servidor (openssl) |
| `scripts/configurar_firewall.sh` | regras do `ufw` |
| `scripts/diagnostico.sh` | retrato do Linux (ip, ss, ps, top, ufw, journal, permissões) |
| `scripts/teste_conteineres.sh` | testes T15 a T18 na topologia com contêineres |
| `scripts/falha_provocada.sh` | T19: o serviço cai no meio de um envio e volta |
| `scripts/captura_trafego.sh` | captura com `tcpdump`, sem e com TLS |
| `scripts/demo_tres_unidades.sh` | demonstração: as 3 unidades enviando ao mesmo tempo |
| `scripts/monitorar.sh` | monitor ao vivo: conexões (`ss`), threads e memória (`ps`) |
| `testes/teste_sistema.py` | testes automatizados T01 a T14 |
| `Dockerfile`, `docker-compose.yml` | imagem e rede das unidades |
| `docs/evidencias/` | saídas reais dos testes, capturas `.pcap` e imagem do painel |
| `docs/checkpoint1.md`, `docs/checkpoint2.md` | situação do projeto em cada checkpoint |

## Requisitos

- Windows 11 com WSL 2 e Ubuntu (testado com Ubuntu 26.04.1 LTS), ou qualquer Linux com systemd.
- Python 3.11 ou mais novo, só biblioteca padrão (nada para instalar com pip).
- No Linux: `docker.io`, `docker-compose-v2`, `ufw`, `openssl`, `tcpdump`, `dnsutils`, `traceroute`.

## Instalação (Ubuntu no WSL 2)

1. Ligue o systemd no WSL, se ainda não estiver ligado. Acrescente ao `/etc/wsl.conf`:

   ```ini
   [boot]
   systemd=true
   ```

   e reinicie o WSL no PowerShell com `wsl --shutdown`.

2. Instale os pacotes e entre no grupo `docker`:

   ```bash
   sudo apt update
   sudo apt install -y docker.io docker-compose-v2 ufw openssl tcpdump dnsutils traceroute
   sudo usermod -aG docker "$USER"     # vale a partir do próximo terminal
   ```

3. Clone o repositório e, na pasta dele, instale o serviço, configure o firewall e suba
   as unidades. Prefira clonar numa pasta do Windows (vista no Ubuntu em `/mnt/c/...`):
   assim o notebook da TI usa a mesma pasta e o mesmo `certs/ca.crt`, que o instalador
   copia para lá.

   ```bash
   cd /mnt/c/Users/<seu-usuario>         # uma pasta do Windows
   git clone https://github.com/0Londero/transferencia-segura-laudos.git
   cd transferencia-segura-laudos
   sudo bash scripts/instalar_servidor.sh     # usuário laudos, /srv/laudos, TLS, systemd
   sudo bash scripts/configurar_firewall.sh   # ufw
   docker compose up -d --build               # contêineres centro, batel e agua-verde
   ```

O WSL desliga a distribuição quando não há terminal aberto; mantenha um terminal do
Ubuntu aberto durante o uso.

## Uso

Nas unidades (contêineres), o servidor, a CA e o token já vêm das variáveis de ambiente:

```bash
docker compose exec centro python src/cliente.py ping
docker compose exec batel sh -c 'head -c 5M /dev/urandom > /home/laudos/laudo.pdf'  # laudo de teste
docker compose exec batel python src/cliente.py enviar /home/laudos/laudo.pdf --destino centro
docker compose exec centro python src/cliente.py listar
docker compose exec centro python src/cliente.py baixar laudo.pdf     # salva em /home/laudos/recebidos
docker compose exec batel sh -c 'head -c 30M /dev/urandom > /home/laudos/grande.pdf'
docker compose exec batel python src/cliente.py enviar /home/laudos/grande.pdf --destino centro --limite 2048
```

`--limite` (KB/s) simula o link lento de uma unidade e deixa tempo para provocar uma
falha no meio do envio (`sudo systemctl stop laudos` e depois `start`).

No Windows (notebook da TI), na pasta do repositório, usando o IP da `eth0` do WSL:

```powershell
python src/cliente.py --servidor 172.19.114.51 --tls-ca certs/ca.crt ping
$env:LTP_TOKEN = "token-batel-2026"
python src/cliente.py --servidor 172.19.114.51 --tls-ca certs/ca.crt --unidade batel enviar laudo.pdf --destino centro
start http://172.19.114.51:8080/      # painel
```

Administração e monitoramento no Ubuntu:

```bash
systemctl status laudos              # estado, PID, memória, tarefas
journalctl -u laudos -f              # log ao vivo (com sudo ou no grupo adm)
sudo ufw status verbose              # regras do firewall
sudo bash scripts/diagnostico.sh     # retrato completo do ambiente
curl -s http://127.0.0.1:8080/metricas
```

## Testes

| Script | Onde roda | Casos |
|---|---|---|
| `testes/teste_sistema.py` | Windows ou contêiner | T01 a T14: PING, concorrência, retomada de envio, SHA-256 divergente, token errado, `../`, servidor fora do ar, download, retomada de download, isolamento entre unidades, HTTP, TLS, limite de conexões e carga |
| `scripts/teste_conteineres.sh` | Ubuntu (sudo) | T15 a T18: rede, 3 contêineres enviando ao mesmo tempo, firewall, download |
| `scripts/falha_provocada.sh` | Ubuntu (sudo) | T19: `systemctl stop` no meio de um envio e retomada |
| `scripts/captura_trafego.sh` | Ubuntu (sudo) | `tcpdump` sem e com TLS |

```powershell
python testes/teste_sistema.py --servidor 172.19.114.51 --tls-ca certs/ca.crt
```

As saídas reais estão em [docs/evidencias/](docs/evidencias/).

## Protocolo LTP/1

Cada mensagem de controle é `[4 bytes: tamanho N, big-endian][N bytes: JSON UTF-8]`,
dentro do canal TLS. Depois de `READY`, os bytes do arquivo seguem crus a partir do `offset`.

| Comando | Campos | Resposta |
|---|---|---|
| `PING` | nenhum | `OK` / `PONG`, versão, servidor, TLS, hora |
| `AUTH` | `unidade`, `token` | `OK` ou `ERR AUTH_INVALIDA` (com atraso de 1 s) |
| `UPLOAD` | `nome`, `destino`, `tamanho`, `sha256` | `READY` + `offset`; depois `OK RECEBIDO` ou `ERR HASH_DIVERGENTE` |
| `DOWNLOAD` | `nome`, `offset` | `READY` + `tamanho`, `sha256`, `offset`; depois os bytes |
| `LIST` | nenhum | laudos da própria unidade (nome, tamanho, sha256) |
| `QUIT` | nenhum | `OK` e fecha a conexão |

Outros erros: `NAO_AUTENTICADO`, `NOME_INVALIDO`, `DESTINO_INVALIDO`, `TAMANHO_INVALIDO`,
`SHA256_INVALIDO`, `OFFSET_INVALIDO`, `NAO_ENCONTRADO`, `OCUPADO`, `LOTADO`,
`COMANDO_DESCONHECIDO`.

**Por que TCP:** um laudo só serve se chegar inteiro e na ordem; o TCP já garante
entrega confiável e ordenada, e a latência não é crítica no cenário. O SHA-256 na
aplicação cobre o que a soma de verificação de 16 bits do TCP não cobre.

**Por que HTTP no painel:** é o protocolo padrão para consulta por navegador e por
ferramentas como `curl`; o painel só lê, então GET basta.

## Bibliotecas

Só biblioteca padrão do Python:

| Biblioteca | Uso |
|---|---|
| `socket` | sockets TCP, `TCP_NODELAY`, `sendfile` (cópia do arquivo pelo kernel) |
| `ssl` | TLS no servidor e no cliente (versão mínima 1.2; na prática negocia o 1.3), verificação da CA e do nome |
| `threading` | thread por cliente, `Lock`, `BoundedSemaphore`, `Event` |
| `http.server` | painel HTTP (`ThreadingHTTPServer`) |
| `hashlib`, `hmac` | SHA-256 dos laudos e dos tokens, comparação em tempo constante |
| `struct`, `json` | enquadramento e corpo das mensagens |
| `os`, `pathlib`, `shutil` | pastas por unidade, permissões, `rename` atômico, disco livre |
| `logging`, `signal`, `argparse` | logs, parada limpa com SIGTERM, parâmetros |
| `urllib`, `statistics`, `tempfile` | só nos testes |

## Segurança

- **TLS 1.3** (negociado; o código aceita 1.2 ou mais novo) com certificado assinado
  pela CA da clínica; o cliente confere a CA e o nome `servidor`. A captura em
  `docs/evidencias/09_captura_trafego.txt` mostra o token e o nome da paciente
  legíveis sem TLS e ausentes com TLS.
- **Integridade:** SHA-256 conferido no servidor (envio) e no cliente (download).
- **Autenticação:** token por unidade; o servidor guarda só o SHA-256 do token.
- **Isolamento:** a pasta do download vem da sessão autenticada, nunca do pedido.
- **Validação de entrada:** nomes restritos (bloqueia `../`), tamanho máximo, destino conhecido.
- **Mínimo privilégio:** serviço com usuário `laudos`, sem capacidades de root,
  sistema de arquivos somente leitura exceto `/srv/laudos` e o log; pastas 750 e
  arquivos 640; chave da CA legível só pelo root; contêineres sem root.
- **Firewall:** entrada bloqueada por padrão; 5050 só para unidades e TI, 8080 só para a TI.
- Tokens em `config/unidades.json` e no `docker-compose.yml` são de laboratório.

## Requisitos do enunciado

| Requisito | Onde está |
|---|---|
| 5.1 Cenário real | seção Cenário |
| 5.2 Dois ou mais nós em rede IP | Windows, Ubuntu (WSL 2) e 3 contêineres |
| 5.3 Plano de rede | seção Arquitetura |
| 5.4 Python com sockets e justificativa | `src/`, seção Protocolo |
| 5.5 Concorrência | thread por conexão (T02, T14, T16) |
| 5.6 Protocolo padronizado | HTTP (painel), TLS e DNS do Docker |
| 5.7 Configuração e monitoramento do Linux | `scripts/`, `deploy/`, `docs/evidencias/` |
| 5.8 Robustez | timeouts, reconexão, retomada, validação, logs (T03, T07, T09, T19) |
| 5.9 Segurança básica | seção Segurança |
| 5.10 Reprodutibilidade | este README, `Dockerfile`, `docker-compose.yml`, scripts |
