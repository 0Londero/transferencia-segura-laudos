"""
Servidor LTP/1 da central de laudos.

Recebe laudos enviados pelas unidades da clínica, confere o SHA-256 de cada
arquivo e guarda o laudo na pasta de entrada da unidade de destino:

    <dados>/<unidade>/entrada/     laudos completos e conferidos (+ arquivo .sha256)
    <dados>/<unidade>/.parciais/   envios incompletos, usados na retomada

A unidade de destino baixa os seus laudos com DOWNLOAD, também com retomada;
o cliente confere o SHA-256 do que recebeu.

Concorrência: uma thread por conexão. Um semáforo limita quantos clientes
são atendidos ao mesmo tempo, para não esgotar memória nem descritores.
Outra thread serve o painel HTTP de monitoramento (monitoramento.py).

Uso (no Linux):
    python3 src/servidor.py --dados ~/laudos                       (sem TLS, laboratório)
    python3 src/servidor.py --dados ~/laudos --tls-cert servidor.crt --tls-chave servidor.key
Em produção, quem inicia o servidor é o systemd (deploy/laudos.service).
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import shutil
import signal
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

import monitoramento
import protocolo as p

RAIZ = Path(__file__).resolve().parent.parent
log = logging.getLogger("servidor")


def resposta_erro(codigo, detalhe, **extra):
    return {"status": "ERR", "erro": codigo, "detalhe": detalhe, **extra}


class Servidor:
    def __init__(self, host, porta, dir_dados, arq_unidades, max_clientes, timeout, tls=None):
        self.host = host
        self.porta = porta
        self.dados = Path(dir_dados).expanduser().resolve()
        self.timeout = timeout
        self.tls = tls                      # ssl.SSLContext ou None
        self.unidades = self._carregar_unidades(arq_unidades)
        self.max_clientes = max_clientes
        self.vagas = threading.BoundedSemaphore(max_clientes)
        self.parar = threading.Event()
        self.escutando = threading.Event()  # usado pelo /saude do painel HTTP
        self.metricas = monitoramento.Metricas()
        # Arquivos .part sendo escritos agora. Evita que duas conexões
        # escrevam no mesmo arquivo parcial ao mesmo tempo.
        self.trava = threading.Lock()
        self.em_uso = set()
        self.comandos = {
            "PING": self.cmd_ping,
            "AUTH": self.cmd_auth,
            "UPLOAD": self.cmd_upload,
            "DOWNLOAD": self.cmd_download,
            "LIST": self.cmd_list,
        }
        self._preparar_diretorios()

    # ================================================================== início

    @staticmethod
    def _carregar_unidades(caminho):
        """Lê {unidade: sha256(token)}. O servidor nunca guarda o token em claro."""
        with open(caminho, encoding="utf-8") as f:
            unidades = json.load(f)["unidades"]
        for nome, hash_token in unidades.items():
            if not p.nome_valido(nome) or not p.sha256_valido(hash_token):
                raise ValueError(f"entrada inválida em {caminho}: {nome!r}")
        return unidades

    def _preparar_diretorios(self):
        # umask 027: arquivos novos saem com 640 e pastas com 750, ou seja,
        # só o usuário do serviço escreve e ninguém de fora do grupo lê.
        os.umask(0o027)
        for unidade in self.unidades:
            for sub in ("entrada", ".parciais"):
                pasta = self.dados / unidade / sub
                pasta.mkdir(parents=True, exist_ok=True)
                os.chmod(pasta, 0o750)

    def servir(self):
        with socket.create_server((self.host, self.porta), backlog=64) as srv:
            # accept() com timeout curto para conseguir parar com Ctrl+C ou SIGTERM
            srv.settimeout(1.0)
            self.escutando.set()
            log.info("%s escutando em %s:%d/TCP (%s) | dados em %s | unidades: %s",
                     p.VERSAO, self.host, self.porta, "TLS" if self.tls else "sem TLS",
                     self.dados, ", ".join(sorted(self.unidades)))
            while not self.parar.is_set():
                try:
                    conn, addr = srv.accept()
                except TimeoutError:
                    continue
                if not self.vagas.acquire(blocking=False):
                    self.metricas.somar("conexoes_recusadas_lotado")
                    log.warning("limite de %d clientes atingido, recusando %s:%d",
                                self.max_clientes, *addr[:2])
                    self._recusar(conn)
                    continue
                threading.Thread(target=self.atender, args=(conn, addr),
                                 name=f"cli-{addr[0]}:{addr[1]}", daemon=True).start()
        self.escutando.clear()
        ativas = self.metricas.retrato()[0]["conexoes_ativas"]
        log.info("servidor encerrado; conexões interrompidas: %d (os envios "
                 "parciais ficam guardados para retomada)", ativas)

    def _recusar(self, conn):
        """Servidor lotado: sem TLS, avisa com LOTADO; com TLS, fecha antes do handshake."""
        with conn:
            if self.tls is None:
                try:
                    conn.settimeout(2)
                    p.enviar_msg(conn, resposta_erro("LOTADO", "tente mais tarde"))
                except OSError:
                    pass
        # Com TLS, fazer o handshake só para recusar ocuparia o laço do accept();
        # o cliente vê a conexão encerrada e tenta de novo com espera exponencial.

    # ============================================================ por conexão

    def atender(self, conn, addr):
        """Atendimento de um cliente. Roda em uma thread própria."""
        sessao = {"unidade": None}
        self.metricas.somar("conexoes_total")
        self.metricas.somar("conexoes_ativas")
        log.info("conexão aceita (threads ativas: %d)", threading.active_count() - 1)
        try:
            conn.settimeout(self.timeout)
            # Sem o algoritmo de Nagle: respostas curtas saem na hora, em vez de
            # esperar o ACK atrasado (40 ms) do outro lado
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self.tls is not None:
                # Handshake dentro da thread do cliente: um cliente lento no
                # handshake não trava o accept() dos outros
                try:
                    conn = self.tls.wrap_socket(conn, server_side=True)
                except ssl.SSLError as erro:
                    self.metricas.somar("falhas_tls")
                    log.warning("handshake TLS recusado: %s", erro.reason or erro)
                    return
                log.info("canal %s estabelecido (%s)", conn.version(), conn.cipher()[0])
            with conn:
                self._laco(conn, sessao)
        except TimeoutError:
            log.warning("cliente inativo por %ss, conexão encerrada", self.timeout)
        except p.ErroProtocolo as erro:
            log.warning("mensagem inválida: %s", erro)
        except OSError as erro:
            log.warning("falha de rede: %s", erro)
        except Exception:
            log.exception("erro inesperado no atendimento")
        finally:
            conn.close()
            self.vagas.release()
            self.metricas.somar("conexoes_ativas", -1)
            log.info("conexão encerrada")

    def _laco(self, conn, sessao):
        """Lê comandos até QUIT ou até o cliente fechar a conexão."""
        while True:
            try:
                msg = p.receber_msg(conn)
            except ConnectionError:
                return  # o cliente fechou a conexão
            cmd = str(msg.get("cmd", "")).upper()
            if cmd == "QUIT":
                p.enviar_msg(conn, {"status": "OK", "msg": "ATE_LOGO"})
                return
            tratador = self.comandos.get(cmd)
            if tratador is None:
                p.enviar_msg(conn, resposta_erro("COMANDO_DESCONHECIDO", cmd))
            elif cmd not in ("PING", "AUTH") and sessao["unidade"] is None:
                p.enviar_msg(conn, resposta_erro("NAO_AUTENTICADO", "envie AUTH antes"))
            else:
                tratador(conn, msg, sessao)

    # =============================================================== comandos

    def cmd_ping(self, conn, msg, sessao):
        p.enviar_msg(conn, {"status": "OK", "msg": "PONG", "versao": p.VERSAO,
                            "servidor": socket.gethostname(), "tls": self.tls is not None,
                            "hora": time.strftime("%Y-%m-%d %H:%M:%S")})

    def cmd_auth(self, conn, msg, sessao):
        unidade = str(msg.get("unidade", ""))
        token = str(msg.get("token", ""))
        recebido = hashlib.sha256(token.encode("utf-8")).hexdigest()
        esperado = self.unidades.get(unidade, "0" * 64)
        # compare_digest leva o mesmo tempo para qualquer entrada (evita ataque de tempo)
        if unidade not in self.unidades or not hmac.compare_digest(recebido, esperado):
            self.metricas.somar("autenticacoes_recusadas")
            self.metricas.evento(f"autenticação recusada (unidade {unidade[:20]!r})")
            log.warning("autenticação recusada (unidade=%r)", unidade[:40])
            time.sleep(1)  # atrasa tentativas de força bruta
            p.enviar_msg(conn, resposta_erro("AUTH_INVALIDA", "unidade ou token incorretos"))
            return
        sessao["unidade"] = unidade
        log.info("unidade %s autenticada", unidade)
        p.enviar_msg(conn, {"status": "OK", "msg": "AUTENTICADO", "unidade": unidade})

    def cmd_upload(self, conn, msg, sessao):
        origem = sessao["unidade"]
        nome, destino = msg.get("nome"), msg.get("destino")
        tamanho, sha = msg.get("tamanho"), msg.get("sha256")

        # Validação de entrada: nada chega ao sistema de arquivos sem passar aqui
        if not p.nome_valido(nome):
            return p.enviar_msg(conn, resposta_erro("NOME_INVALIDO", repr(nome)[:80]))
        if destino not in self.unidades:
            return p.enviar_msg(conn, resposta_erro("DESTINO_INVALIDO", repr(destino)[:80]))
        if not p.tamanho_valido(tamanho):
            return p.enviar_msg(conn, resposta_erro("TAMANHO_INVALIDO", repr(tamanho)[:80]))
        if not p.sha256_valido(sha):
            return p.enviar_msg(conn, resposta_erro("SHA256_INVALIDO", repr(sha)[:80]))

        pasta = self.dados / destino
        final = pasta / "entrada" / nome
        if final.exists() and self._hash_registrado(final) == sha:
            log.info("%s -> %s: %s já recebido antes, nada a fazer", origem, destino, nome)
            return p.enviar_msg(conn, {"status": "OK", "msg": "JA_RECEBIDO",
                                       "arquivo": nome, "sha256": sha})

        # O parcial é identificado por origem + hash: só retoma o MESMO conteúdo
        parcial = pasta / ".parciais" / f"{origem}_{sha}.part"
        with self.trava:
            if parcial in self.em_uso:
                return p.enviar_msg(conn, resposta_erro("OCUPADO", "envio em andamento"))
            self.em_uso.add(parcial)
        try:
            offset = parcial.stat().st_size if parcial.exists() else 0
            if offset > tamanho:  # parcial corrompido, recomeça do zero
                parcial.unlink()
                offset = 0
            p.enviar_msg(conn, {"status": "READY", "offset": offset})
            if offset:
                self.metricas.somar("uploads_retomados")
                log.info("%s -> %s: retomando %s no byte %d de %d",
                         origem, destino, nome, offset, tamanho)
            else:
                log.info("%s -> %s: recebendo %s (%d bytes)", origem, destino, nome, tamanho)

            inicio = time.perf_counter()
            try:
                self._receber_dados(conn, parcial, tamanho - offset)
            except OSError:  # queda, timeout ou erro no TLS
                recebido = parcial.stat().st_size
                self.metricas.somar("uploads_interrompidos")
                self.metricas.somar("bytes_recebidos", recebido - offset)
                self.metricas.evento(f"{origem} -> {destino}: {nome} interrompido em "
                                     f"{recebido * 100 // max(tamanho, 1)}%, parcial guardado")
                log.warning("%s -> %s: envio de %s interrompido com %d de %d bytes; "
                            "parcial mantido para retomada", origem, destino, nome,
                            recebido, tamanho)
                raise
            duracao = time.perf_counter() - inicio
            self.metricas.somar("bytes_recebidos", tamanho - offset)

            calculado = p.sha256_arquivo(parcial)
            if calculado != sha:
                parcial.unlink()
                self.metricas.somar("uploads_hash_divergente")
                self.metricas.evento(f"{origem} -> {destino}: {nome} REJEITADO, SHA-256 não confere")
                log.error("%s -> %s: SHA-256 de %s não confere (esperado %s, calculado %s); "
                          "arquivo descartado", origem, destino, nome, sha[:16], calculado[:16])
                return p.enviar_msg(conn, resposta_erro(
                    "HASH_DIVERGENTE", "arquivo corrompido no envio",
                    esperado=sha, calculado=calculado))

            # Nunca sobrescreve um laudo diferente com o mesmo nome
            if final.exists():
                final = self._nome_livre(final)
            os.replace(parcial, final)  # rename atômico dentro do mesmo sistema de arquivos
            os.chmod(final, 0o640)
            # Formato do sha256sum: dá para conferir no Linux com "sha256sum -c"
            Path(f"{final}.sha256").write_text(f"{sha}  {final.name}\n", encoding="utf-8")

            taxa = (tamanho - offset) / duracao / 1e6 if duracao > 0 else 0.0
            self.metricas.somar("uploads_ok")
            self.metricas.evento(f"{origem} -> {destino}: {final.name} recebido "
                                 f"({tamanho / 1e6:.1f} MB, SHA-256 ok"
                                 f"{', retomado' if offset else ''})")
            log.info("%s -> %s: %s gravado, SHA-256 confere (%d bytes, %.1f MB/s)",
                     origem, destino, final.name, tamanho, taxa)
            p.enviar_msg(conn, {"status": "OK", "msg": "RECEBIDO", "arquivo": final.name,
                                "sha256": sha, "offset_inicial": offset})
        finally:
            with self.trava:
                self.em_uso.discard(parcial)

    def cmd_download(self, conn, msg, sessao):
        """A unidade baixa um laudo da PRÓPRIA entrada, a partir do offset que já tem."""
        unidade = sessao["unidade"]
        nome, offset = msg.get("nome"), msg.get("offset", 0)
        if not p.nome_valido(nome) or nome.endswith(".sha256"):
            return p.enviar_msg(conn, resposta_erro("NOME_INVALIDO", repr(nome)[:80]))
        if not p.tamanho_valido(offset):
            return p.enviar_msg(conn, resposta_erro("OFFSET_INVALIDO", repr(offset)[:80]))
        # A pasta vem da sessão autenticada, nunca do pedido: uma unidade não
        # consegue ler a caixa de entrada de outra
        arquivo = self.dados / unidade / "entrada" / nome
        if not arquivo.is_file():
            return p.enviar_msg(conn, resposta_erro("NAO_ENCONTRADO", nome))

        tamanho = arquivo.stat().st_size
        sha = self._hash_registrado(arquivo) or p.sha256_arquivo(arquivo)
        if offset > tamanho:  # o parcial do cliente não é deste arquivo: recomeça
            offset = 0
        p.enviar_msg(conn, {"status": "READY", "tamanho": tamanho, "sha256": sha,
                            "offset": offset})
        log.info("%s: enviando %s a partir do byte %d de %d", unidade, nome, offset, tamanho)
        enviados = 0
        try:
            if tamanho > offset:
                with open(arquivo, "rb") as f:
                    enviados = conn.sendfile(f, offset, tamanho - offset)
        except OSError:
            self.metricas.somar("downloads_interrompidos")
            log.warning("%s: download de %s interrompido", unidade, nome)
            raise
        finally:
            self.metricas.somar("bytes_enviados", enviados)
        self.metricas.somar("downloads_ok")
        self.metricas.evento(f"{unidade} baixou {nome}"
                             f"{f' (retomado no byte {offset})' if offset else ''}")
        log.info("%s: %s enviado (%d bytes)", unidade, nome, enviados)

    def cmd_list(self, conn, msg, sessao):
        """Lista os laudos que chegaram para a unidade autenticada."""
        pasta = self.dados / sessao["unidade"] / "entrada"
        arquivos = [{"nome": arq.name, "tamanho": arq.stat().st_size,
                     "sha256": self._hash_registrado(arq)}
                    for arq in sorted(pasta.iterdir())
                    if arq.is_file() and arq.suffix != ".sha256"]
        p.enviar_msg(conn, {"status": "OK", "arquivos": arquivos})

    # ========================================================= monitoramento

    def saude(self):
        no_ar = self.escutando.is_set() and not self.parar.is_set()
        return {"status": "ok" if no_ar else "fora", "versao": p.VERSAO,
                "servidor": socket.gethostname(), "tls": self.tls is not None,
                "uptime_s": round(time.time() - self.metricas.inicio)}

    def estado(self):
        """Tudo o que o painel mostra; também é o corpo de GET /metricas."""
        contadores, eventos = self.metricas.retrato()
        disco = shutil.disk_usage(self.dados)
        return {
            "servidor": socket.gethostname(), "versao": p.VERSAO, "porta": self.porta,
            "tls": self.tls is not None, "limite_clientes": self.max_clientes,
            "uptime_s": round(time.time() - self.metricas.inicio),
            "contadores": contadores,
            "processo": self._processo(),
            "unidades": {u: self._resumo_unidade(u) for u in sorted(self.unidades)},
            "disco": {"livre_gb": round(disco.free / 1e9, 1),
                      "total_gb": round(disco.total / 1e9, 1)},
            "eventos": eventos,
        }

    @staticmethod
    def _processo():
        """Recursos do próprio processo, lidos do /proc (os mesmos números do ps e do top)."""
        tempos = os.times()
        info = {"pid": os.getpid(), "threads": threading.active_count(),
                "memoria_rss_kb": 0, "cpu_s": round(tempos.user + tempos.system, 2)}
        try:
            with open("/proc/self/status", encoding="ascii") as f:
                for linha in f:
                    if linha.startswith("VmRSS:"):
                        info["memoria_rss_kb"] = int(linha.split()[1])
                    elif linha.startswith("Threads:"):
                        info["threads"] = int(linha.split()[1])
        except OSError:
            pass  # fora do Linux não há /proc
        if hasattr(os, "getloadavg"):
            info["carga_1min"] = round(os.getloadavg()[0], 2)
        return info

    def _resumo_unidade(self, unidade):
        laudos = volume = 0
        for arq in (self.dados / unidade / "entrada").iterdir():
            try:
                if arq.is_file() and arq.suffix != ".sha256":
                    laudos += 1
                    volume += arq.stat().st_size
            except OSError:
                continue  # arquivo removido durante a contagem
        parciais = sum(1 for _ in (self.dados / unidade / ".parciais").glob("*.part"))
        return {"laudos": laudos, "bytes": volume, "parciais": parciais}

    # ============================================================== auxiliares

    @staticmethod
    def _receber_dados(conn, parcial, n):
        """Acrescenta n bytes do socket ao arquivo parcial (modo append)."""
        with open(parcial, "ab") as f:
            faltam = n
            while faltam > 0:
                bloco = conn.recv(min(faltam, p.TAM_BLOCO))
                if not bloco:
                    raise ConnectionError("cliente desconectou no meio do envio")
                f.write(bloco)
                faltam -= len(bloco)

    @staticmethod
    def _hash_registrado(arquivo):
        try:
            return Path(f"{arquivo}.sha256").read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            return None

    @staticmethod
    def _nome_livre(caminho):
        n = 2
        while True:
            candidato = caminho.with_name(f"{caminho.stem}_{n}{caminho.suffix}")
            if not candidato.exists():
                return candidato
            n += 1


def configurar_log(arquivo):
    formato = "%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s"
    tela = logging.StreamHandler()
    # Sob o systemd a saída vai para o journal, que já registra data, hora e PID
    tela.setFormatter(logging.Formatter(
        "%(levelname)-7s [%(threadName)s] %(message)s" if os.environ.get("JOURNAL_STREAM")
        else formato))
    handlers = [tela]
    if arquivo:
        Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
        em_arquivo = logging.FileHandler(arquivo, encoding="utf-8")
        em_arquivo.setFormatter(logging.Formatter(formato))
        handlers.append(em_arquivo)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def main():
    ap = argparse.ArgumentParser(description="Servidor LTP/1 de transferência segura de laudos")
    ap.add_argument("--host", default="0.0.0.0", help="endereço de escuta (padrão: todas as interfaces)")
    ap.add_argument("--porta", type=int, default=p.PORTA_PADRAO)
    ap.add_argument("--dados", default=str(RAIZ / "dados"), help="pasta raiz dos laudos")
    ap.add_argument("--unidades", default=str(RAIZ / "config" / "unidades.json"))
    ap.add_argument("--max-clientes", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=30.0, help="inatividade máxima por conexão (s)")
    ap.add_argument("--log", default=str(RAIZ / "logs" / "servidor.log"),
                    help="arquivo de log (vazio para usar só a saída padrão)")
    ap.add_argument("--tls-cert", help="certificado do servidor (PEM); liga o TLS")
    ap.add_argument("--tls-chave", help="chave privada do certificado (PEM)")
    ap.add_argument("--http-host", default="0.0.0.0", help="endereço do painel HTTP")
    ap.add_argument("--http-porta", type=int, default=p.PORTA_HTTP_PADRAO,
                    help="porta do painel HTTP (0 desliga)")
    args = ap.parse_args()
    if bool(args.tls_cert) != bool(args.tls_chave):
        ap.error("o TLS exige --tls-cert e --tls-chave juntos")

    configurar_log(args.log)
    try:
        tls = p.contexto_tls_servidor(args.tls_cert, args.tls_chave) if args.tls_cert else None
        servidor = Servidor(args.host, args.porta, args.dados, args.unidades,
                            args.max_clientes, args.timeout, tls)
        httpd = None
        if args.http_porta:
            httpd = monitoramento.iniciar_http(servidor, args.http_host, args.http_porta)
            log.info("painel HTTP em http://%s:%d/ (rotas /saude e /metricas)",
                     args.http_host, args.http_porta)
    except (OSError, ValueError, KeyError, ssl.SSLError) as erro:
        # Porta ocupada, certificado ausente, configuração inválida: falha clara
        # e código de saída 1, que o systemd mostra no "systemctl status"
        log.critical("não foi possível iniciar o servidor: %s", erro)
        return 1

    # SIGTERM é o sinal que o systemd e o "docker stop" mandam para parar o serviço
    signal.signal(signal.SIGTERM, lambda *_: servidor.parar.set())
    try:
        servidor.servir()
    except KeyboardInterrupt:
        log.info("interrompido pelo usuário")
    except OSError as erro:
        log.critical("falha ao abrir a porta %d: %s", args.porta, erro)
        return 1
    finally:
        if httpd is not None:
            httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
