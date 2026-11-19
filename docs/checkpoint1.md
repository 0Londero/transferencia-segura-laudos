# Checkpoint 1 (09/11/2026)

> Registro histórico do que foi apresentado em 09/11. A situação atual está em
> [checkpoint2.md](checkpoint2.md); a suíte de testes atual é `testes/teste_sistema.py`.

O enunciado pede para o Checkpoint 1: tema aprovado, arquitetura e plano de rede
definidos, ambiente montado e primeira comunicação cliente/servidor funcionando.

| Item | Situação |
|---|---|
| Tema | Projeto 4, transferência segura de arquivos. **Confirmar a aprovação com o professor em sala.** |
| Arquitetura e plano de rede | Definidos (seções 2 e 3 abaixo) |
| Ambiente montado | WSL 2: servidor no Ubuntu 26.04, clientes no Windows 11 |
| Primeira comunicação | Funcionando, com 7 de 7 testes aprovados (seção 4) |

## 1. Tema e cenário

**Título:** Transferência segura de laudos entre unidades de uma clínica.

**Integrantes:** Otávio Londero, Davi Berbek e Pedro Joaquim Freire de Lima.

**Cenário (parágrafo do registro no Canvas):** uma clínica de diagnóstico por imagem
com três unidades em Curitiba (Centro, Batel e Água Verde) precisa trocar laudos entre
as unidades todos os dias. Hoje isso é feito por e-mail ou pen drive, sem garantia de
que o arquivo chegou íntegro, sem retomada quando a conexão cai e sem registro de quem
enviou o quê. A solução é um servidor central em Linux que recebe os laudos por TCP,
confere o SHA-256 de cada arquivo, retoma envios interrompidos, separa os laudos em
diretórios por unidade com permissões restritas e atende várias unidades ao mesmo tempo.

## 2. Arquitetura

```
 +==============================+                      +================================+
 |  Host Windows 11             |                      |  Ubuntu 26.04 (WSL 2)          |
 |  clientes das unidades       |    rede virtual      |  servidor LTP/1                |
 |  centro, batel, agua-verde   | <==================> |  eth0 172.19.114.51/20         |
 |  vEthernet 172.19.112.1/20   |    NAT do WSL        |  porta 5050/TCP                |
 +==============================+   172.19.112.0/20    |  ~/laudos/<unidade>/entrada/   |
                                                       +================================+
```

- **Transporte:** TCP, porque o laudo precisa chegar inteiro e em ordem; a latência
  não é crítica.
- **Aplicação:** protocolo próprio LTP/1 (prefixo de 4 bytes + JSON), com os comandos
  `PING`, `AUTH`, `UPLOAD`, `LIST` e `QUIT`. Detalhes no [README](../README.md).
- **Concorrência:** uma thread por conexão, limitada por semáforo (20 clientes).
- **Armazenamento:** `<dados>/<unidade>/entrada/` (laudos conferidos, com arquivo
  `.sha256` no formato do `sha256sum`) e `<dados>/<unidade>/.parciais/` (retomada).

## 3. Plano de rede

| Nó | Função | IP/máscara | Gateway | Porta |
|---|---|---|---|---|
| Host Windows 11 | clientes das 3 unidades | 172.19.112.1/20 (255.255.240.0) | não se aplica | efêmera |
| Ubuntu no WSL 2 | servidor LTP/1 | 172.19.114.51/20 (255.255.240.0) | 172.19.112.1 | 5050/TCP |

Topologia final, para o Checkpoint 2 (arquivos já em `docker-compose.yml`):
rede bridge `rede_clinica` 172.28.0.0/24, gateway 172.28.0.1, servidor em .10 e
unidades em .21 (centro), .22 (batel) e .23 (agua-verde).

## 4. Evidências

Saídas completas em [evidencias/](evidencias/):
`01_cliente_windows.txt` (testes vistos do cliente), `02_diagnostico_linux.txt`
(`ip`, `ss`, `ps`, permissões e `sha256sum -c` no servidor) e `03_servidor.log`.

| ID | Teste | Resultado |
|---|---|---|
| T01 | Primeira comunicação (PING Windows para WSL) | PONG em 1,9 ms |
| T02 | 3 unidades enviando 20 MB ao mesmo tempo | 3 laudos gravados em 0,29 s; `ss` mostra 3 conexões ESTAB e `ps` mostra 4 threads |
| T03 | Queda forçada após 4 MiB de 10 MiB | Retomou no byte 4.194.304; SHA-256 confere |
| T04 | UPLOAD com SHA-256 errado | `HASH_DIVERGENTE`, arquivo descartado |
| T05 | Token errado | `AUTH_INVALIDA` |
| T06 | Nome `../../etc/passwd` | `NOME_INVALIDO` |
| T07 | Servidor fora do ar | Conexão recusada tratada após 3 tentativas |

## 5. Requisitos do enunciado (seção 5)

| Requisito | Situação |
|---|---|
| 5.1 Cenário real | Feito |
| 5.2 Dois ou mais nós em rede IP | Feito (WSL 2); Docker Compose preparado, não validado |
| 5.3 Plano de rede | Feito para o WSL; Docker planejado |
| 5.4 Python com sockets e justificativa do transporte | Feito |
| 5.5 Concorrência | Feito (thread por conexão, testado com 3 clientes) |
| 5.6 Protocolo padronizado (HTTP, DNS etc.) | **Pendente:** endpoint HTTP de saúde e métricas; DNS do Docker para achar o servidor pelo nome |
| 5.7 Configuração e monitoramento do Linux | Parcial: `ip`, `ss`, `ps`, permissões. Falta usuário dedicado, systemd, `journalctl`, `tcpdump` |
| 5.8 Robustez | Feito: timeouts, reconexão com espera exponencial, retomada, validação, logs |
| 5.9 Segurança básica | Parcial: token, SHA-256, validação, permissões. Falta firewall (`ufw`) e, se der tempo, TLS |
| 5.10 Reprodutibilidade | Parcial: README, Dockerfile e compose. **Falta criar o repositório no GitHub** |

## 6. Próximos passos até o Checkpoint 2 (23/11)

Lembrete: 16/11 é a Avaliação Somativa 02, sem atividade do projeto em sala.

1. Criar o repositório no GitHub e subir esta pasta.
2. Instalar o Docker no WSL (`sudo apt install docker.io docker-compose-v2`) e rodar
   os testes T01 a T07 entre contêineres.
3. Requisito 5.6: endpoint HTTP (`/saude`, `/metricas`) no servidor, porta 8080.
4. Linux: usuário `laudos`, serviço `systemd`, logs no `journalctl`, `ufw` liberando
   só a porta 5050 para a rede da clínica, captura com `tcpdump` aberta no Wireshark.
5. Comando `DOWNLOAD` para a unidade de destino baixar o laudo conferindo o SHA-256.
6. TLS no canal (módulo `ssl`), já que laudos são dados sensíveis (LGPD).
7. Roteiro do vídeo e preenchimento do relatório (`report/pdf/report.tex`).

## 7. Roteiro da demonstração em sala

```bash
# Terminal 1, Ubuntu (WSL)
ip -br addr show eth0
cd /mnt/c/pjbl_con_ciber/projeto
python3 src/servidor.py --dados ~/laudos
```

```powershell
# Terminal 2, Windows (pasta projeto), trocar o IP pelo mostrado no passo anterior
python src/cliente.py --servidor 172.19.114.51 ping
python testes/teste_checkpoint1.py --servidor 172.19.114.51
```

```bash
# Terminal 3, Ubuntu (WSL)
bash scripts/diagnostico.sh 172.19.112.1 ~/laudos
cd ~/laudos/centro/entrada && sha256sum -c *.sha256
```
