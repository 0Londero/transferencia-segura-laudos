# Checkpoint 2 (23/11/2026)

O enunciado pede para o Checkpoint 2: solução completa, testes em andamento, roteiro
do vídeo pronto e dúvidas finais.

| Item | Situação |
|---|---|
| Solução completa | Feita: servidor como serviço do systemd com TLS, painel HTTP, download, firewall e 3 unidades em contêineres |
| Testes | 19 casos executados e aprovados (T01 a T19), evidências em [evidencias/](evidencias/) |
| Roteiro do vídeo | Pronto: 9:30 em 8 blocos, 3:10 de fala para cada integrante |
| Repositório | https://github.com/0Londero/transferencia-segura-laudos |

## 1. O que mudou desde o Checkpoint 1

| Antes (09/11) | Agora |
|---|---|
| Servidor rodando no terminal, com o usuário pessoal | Serviço `laudos` do systemd, usuário de sistema `laudos`, sem capacidades de root |
| Canal sem criptografia | TLS 1.3 com CA da clínica; cliente confere a CA e o nome do servidor |
| Só envio (UPLOAD) | Também DOWNLOAD com retomada e conferência do SHA-256 no cliente |
| Sem protocolo padronizado | Painel HTTP com `/`, `/saude` e `/metricas` (requisito 5.6) |
| Sem firewall | `ufw`: entrada bloqueada; 5050 para unidades e TI; 8080 só para a TI |
| Clientes no Windows | 3 unidades em contêineres Docker com IP fixo, mais o notebook da TI no Windows |
| Logs só em arquivo | Journal do systemd (`journalctl -u laudos`) e arquivo em `/var/log/laudos` |
| 7 testes | 19 testes, incluindo carga, TLS, firewall e falha provocada com `systemctl stop` |

## 2. Requisitos do enunciado (seção 5)

| Requisito | Situação | Evidência |
|---|---|---|
| 5.1 Cenário real | Feito | README |
| 5.2 Dois ou mais nós em rede IP | Feito: Windows, Ubuntu (WSL 2) e 3 contêineres | `07_testes_conteineres.txt` |
| 5.3 Plano de rede | Feito | README, seção Arquitetura |
| 5.4 Python com sockets e justificativa | Feito | `src/`, README |
| 5.5 Concorrência | Feito: thread por conexão, limite de 20 | T02, T13, T14, T16 |
| 5.6 Protocolo padronizado | Feito: HTTP (painel); também TLS e DNS do Docker | T11, `08_http.txt`, `08_painel_http.png` |
| 5.7 Configuração e monitoramento do Linux | Feito: usuário, permissões, systemd, journal, ufw, ip, ss, ps, top, ping, traceroute, dig, tcpdump, Wireshark | `04`, `05`, `09`, `11` |
| 5.8 Robustez | Feito: timeouts, reconexão, retomada, validação, logs | T03, T07, T09, T19 |
| 5.9 Segurança básica | Feito: TLS, token, SHA-256, firewall, mínimo privilégio | T04, T05, T06, T10, T12, T17 |
| 5.10 Reprodutibilidade | Feito: repositório, README, Dockerfile, compose, scripts, unidade do systemd | GitHub |

## 3. Pendências até a entrega (25/11)

1. Gravação do vídeo (até 10 min, todos em câmera, demonstração ao vivo), seguindo o roteiro.
2. Link do vídeo no documento-resumo e revisão da declaração de uso de IA.
3. Conferir os links do vídeo e do repositório numa janela anônima.
4. Enviar no Canvas o PDF e o link do vídeo no campo de texto.

## 4. Dúvidas para levar ao professor

1. O servidor como serviço no Linux do WSL e as unidades em contêineres Docker
   atendem o requisito de dois ou mais nós, ou ele prefere outra divisão?
2. O painel HTTP só de leitura basta para o requisito 5.6 (protocolo padronizado)?
3. A declaração de uso de IA segue algum modelo das regras gerais da disciplina?
