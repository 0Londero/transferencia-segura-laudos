"""
Cliente LTP/1, usado pelas unidades da clínica.

Exemplos:
    python src/cliente.py --servidor 172.19.114.51 ping
    python src/cliente.py --servidor 172.19.114.51 --unidade batel enviar laudo.pdf --destino centro
    python src/cliente.py --servidor 172.19.114.51 --unidade centro listar
    python src/cliente.py --servidor 172.19.114.51 --unidade centro baixar laudo.pdf

Com TLS (servidor iniciado com certificado), acrescente --tls-ca certs/ca.crt.

O token da unidade vem de --token ou, de preferência, da variável de ambiente
LTP_TOKEN (argumentos de linha de comando aparecem no "ps" de outros usuários).
"""

import argparse
import logging
import os
import socket
import ssl
import sys
import time
from pathlib import Path

import protocolo as p

log = logging.getLogger("cliente")


class ErroServidor(Exception):
    """O servidor respondeu com status ERR."""

    def __init__(self, resposta):
        self.codigo = resposta.get("erro", "DESCONHECIDO")
        self.resposta = resposta
        super().__init__(f"{self.codigo}: {resposta.get('detalhe', '')}")


class ErroIntegridade(Exception):
    """O arquivo baixado não bate com o SHA-256 informado pelo servidor."""


def falha_passageira(erro):
    """
    Falhas de rede que valem uma nova tentativa: queda, recusa, timeout e erros
    do TLS no meio da conexão (com TLS, o servidor caindo durante um envio pode
    aparecer como SSLEOFError). Certificado não confiável não é passageiro.
    """
    if isinstance(erro, ssl.SSLCertVerificationError):
        return False
    return isinstance(erro, (ConnectionError, TimeoutError, ssl.SSLError))


class Cliente:
    def __init__(self, servidor, porta=p.PORTA_PADRAO, unidade=None, token=None,
                 timeout=10.0, tentativas=5, tls=None, tls_nome=p.NOME_TLS_PADRAO):
        self.servidor = servidor
        self.porta = porta
        self.unidade = unidade
        self.token = token
        self.timeout = timeout
        self.tentativas = tentativas
        self.tls = tls              # ssl.SSLContext (com a CA da clínica) ou None
        self.tls_nome = tls_nome    # nome conferido no certificado do servidor

    # ================================================================ conexão

    def conectar(self):
        """Abre a conexão (TCP, TLS e AUTH) com reconexão e espera exponencial (1, 2, 4, 8 s...)."""
        espera = 1
        for tentativa in range(1, self.tentativas + 1):
            try:
                return self._abrir()
            except ssl.SSLCertVerificationError:
                raise  # certificado não confiável: tentar de novo não resolve
            except (OSError, ErroServidor) as erro:
                # Recusada, timeout, host inalcançável, DNS, queda no handshake e
                # servidor lotado são passageiros; token errado, por exemplo, não é
                if isinstance(erro, ErroServidor) and erro.codigo != "LOTADO":
                    raise
                log.warning("tentativa %d/%d de conectar a %s:%d falhou: %s",
                            tentativa, self.tentativas, self.servidor, self.porta, erro)
                if tentativa == self.tentativas:
                    raise
                time.sleep(espera)
                espera = min(espera * 2, 16)

    def _abrir(self):
        sock = socket.create_connection((self.servidor, self.porta), timeout=self.timeout)
        try:
            # Desliga o Nagle: com TLS, o comando logo após o handshake ficava
            # retido até o ACK atrasado do servidor (cerca de 40 ms por pedido)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self.tls is not None:
                # Handshake TLS: confere a cadeia até a CA da clínica e o nome do servidor
                sock = self.tls.wrap_socket(sock, server_hostname=self.tls_nome)
            local = sock.getsockname()
            canal = f", {sock.version()}" if self.tls is not None else ""
            log.info("conectado a %s:%d (porta local %s:%d%s)",
                     self.servidor, self.porta, *local[:2], canal)
            if self.unidade:
                self._pedir(sock, {"cmd": "AUTH", "unidade": self.unidade,
                                   "token": self.token or ""})
            return sock
        except BaseException:
            sock.close()
            raise

    @staticmethod
    def _pedir(sock, mensagem):
        """Envia um comando e devolve a resposta; ERR vira exceção."""
        p.enviar_msg(sock, mensagem)
        resposta = p.receber_msg(sock)
        if resposta.get("status") == "ERR":
            raise ErroServidor(resposta)
        return resposta

    # =============================================================== comandos

    def ping(self):
        with self.conectar() as sock:
            inicio = time.perf_counter()
            resposta = self._pedir(sock, {"cmd": "PING"})
            rtt_ms = (time.perf_counter() - inicio) * 1000
            p.enviar_msg(sock, {"cmd": "QUIT"})
        return resposta, rtt_ms

    def listar(self):
        with self.conectar() as sock:
            resposta = self._pedir(sock, {"cmd": "LIST"})
            p.enviar_msg(sock, {"cmd": "QUIT"})
        return resposta["arquivos"]

    def enviar(self, caminho, destino, simular_queda=None, limite_kbs=None):
        """
        Envia um laudo. Se a conexão cair no meio, reconecta e o servidor
        informa quantos bytes já tem (offset); o envio continua dali.
        simular_queda: derruba a conexão de propósito depois de N bytes
        (só na primeira tentativa), para demonstrar a retomada.
        limite_kbs: limita a taxa de envio, para simular o link de uma unidade.
        """
        caminho = Path(caminho)
        tamanho = caminho.stat().st_size
        sha = p.sha256_arquivo(caminho)
        log.info("laudo %s: %d bytes, SHA-256 %s", caminho.name, tamanho, sha)
        pedido = {"cmd": "UPLOAD", "nome": caminho.name, "destino": destino,
                  "tamanho": tamanho, "sha256": sha}

        for tentativa in range(1, self.tentativas + 1):
            try:
                # conectar() já tenta de novo com espera exponencial; se o servidor
                # continuar fora do ar, este laço tenta outra rodada, como no download
                with self.conectar() as sock:
                    resposta = self._pedir(sock, pedido)
                    if resposta["status"] == "OK":  # o servidor já tinha esse laudo
                        log.info("servidor já possui %s com o mesmo SHA-256", caminho.name)
                        p.enviar_msg(sock, {"cmd": "QUIT"})
                        return resposta
                    offset = resposta["offset"]
                    if offset:
                        log.info("servidor já tem %d bytes, retomando a partir daí", offset)
                    queda = simular_queda if tentativa == 1 else None
                    self._enviar_bytes(sock, caminho, offset, tamanho, queda, limite_kbs)
                    resposta = p.receber_msg(sock)
                    if resposta.get("status") == "ERR":
                        raise ErroServidor(resposta)
                    p.enviar_msg(sock, {"cmd": "QUIT"})
                    log.info("servidor confirmou %s (SHA-256 conferido)", resposta["arquivo"])
                    return resposta
            except OSError as erro:
                if not falha_passageira(erro):
                    raise
                log.warning("envio interrompido (%s); tentativa %d/%d",
                            erro, tentativa, self.tentativas)
            except ErroServidor as erro:
                # Logo após uma queda, o servidor pode ainda estar fechando a
                # conexão antiga; OCUPADO é temporário, então vale tentar de novo
                if erro.codigo != "OCUPADO":
                    raise
                log.warning("servidor ainda finaliza o envio anterior; tentativa %d/%d",
                            tentativa, self.tentativas)
            if tentativa < self.tentativas:  # depois da última, não há por que esperar
                time.sleep(min(2 ** (tentativa - 1), 16))
        raise ConnectionError(f"não foi possível concluir o envio em {self.tentativas} tentativas")

    def baixar(self, nome, pasta, simular_queda=None):
        """
        Baixa um laudo da entrada da unidade. Os bytes vão para <nome>.part;
        se a conexão cair, a próxima tentativa pede só o que falta (offset).
        No fim, o SHA-256 do arquivo é conferido com o informado pelo servidor.
        """
        if not p.nome_valido(nome):
            raise ValueError(f"nome de laudo inválido: {nome!r}")
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        final, parcial = pasta / nome, pasta / f"{nome}.part"

        for tentativa in range(1, self.tentativas + 1):
            offset = parcial.stat().st_size if parcial.exists() else 0
            try:
                with self.conectar() as sock:
                    resposta = self._pedir(sock, {"cmd": "DOWNLOAD", "nome": nome,
                                                  "offset": offset})
                    tamanho, sha, offset = (resposta["tamanho"], resposta["sha256"],
                                            resposta["offset"])
                    if offset:
                        log.info("já há %d de %d bytes em disco, retomando a partir daí",
                                 offset, tamanho)
                    queda = simular_queda if tentativa == 1 else None
                    self._receber_arquivo(sock, parcial, offset, tamanho, queda)
                    p.enviar_msg(sock, {"cmd": "QUIT"})
            except OSError as erro:
                if not falha_passageira(erro):
                    raise
                log.warning("download interrompido (%s); tentativa %d/%d",
                            erro, tentativa, self.tentativas)
                if tentativa < self.tentativas:
                    time.sleep(min(2 ** (tentativa - 1), 16))
                continue

            calculado = p.sha256_arquivo(parcial)
            if calculado != sha:
                parcial.unlink()
                raise ErroIntegridade(f"SHA-256 de {nome} não confere: esperado {sha[:16]}..., "
                                      f"calculado {calculado[:16]}...; arquivo descartado")
            os.replace(parcial, final)
            Path(f"{final}.sha256").write_text(f"{sha}  {nome}\n", encoding="utf-8")
            log.info("%s baixado e conferido (SHA-256 %s)", nome, sha)
            return {"arquivo": str(final), "tamanho": tamanho, "sha256": sha,
                    "offset_inicial": offset}
        raise ConnectionError(f"não foi possível concluir o download em {self.tentativas} tentativas")

    # ============================================================= auxiliares

    @staticmethod
    def _enviar_bytes(sock, caminho, offset, tamanho, queda=None, limite_kbs=None):
        simular = queda is not None and offset < queda < tamanho
        fim = queda if simular else tamanho
        with open(caminho, "rb") as f:
            if limite_kbs:
                Cliente._enviar_limitado(sock, f, offset, fim - offset, limite_kbs)
            elif fim > offset:
                # socket.sendfile usa a chamada os.sendfile no Linux: o kernel copia
                # do arquivo direto para o socket, sem passar pela memória do Python
                sock.sendfile(f, offset, fim - offset)
        if simular:
            log.warning("simulando queda de conexão após %d bytes", queda)
            raise ConnectionAbortedError("queda simulada pelo teste")

    @staticmethod
    def _enviar_limitado(sock, f, offset, n, limite_kbs):
        """Envia n bytes a no máximo limite_kbs KB/s (simula o link lento de uma unidade)."""
        taxa = limite_kbs * 1024
        f.seek(offset)
        inicio, enviados, proximo_aviso = time.monotonic(), 0, 0.25
        while enviados < n:
            bloco = f.read(min(p.TAM_BLOCO, n - enviados))
            if not bloco:
                break
            sock.sendall(bloco)
            enviados += len(bloco)
            if enviados / n >= proximo_aviso:
                log.info("enviados %d%% (%d de %d bytes desta conexão)",
                         enviados * 100 // n, enviados, n)
                proximo_aviso += 0.25
            adiantado = enviados / taxa - (time.monotonic() - inicio)
            if adiantado > 0:
                time.sleep(adiantado)

    @staticmethod
    def _receber_arquivo(sock, parcial, offset, tamanho, queda=None):
        with open(parcial, "ab" if offset else "wb") as f:
            recebidos = offset
            while recebidos < tamanho:
                bloco = sock.recv(min(tamanho - recebidos, p.TAM_BLOCO))
                if not bloco:
                    raise ConnectionError("servidor encerrou a conexão no meio do download")
                f.write(bloco)
                recebidos += len(bloco)
                if queda is not None and offset < queda <= recebidos < tamanho:
                    log.warning("simulando queda de conexão após %d bytes", recebidos)
                    raise ConnectionAbortedError("queda simulada pelo teste")


def main():
    ap = argparse.ArgumentParser(description="Cliente LTP/1 de transferência segura de laudos")
    ap.add_argument("--servidor", default=os.environ.get("LTP_SERVIDOR", "127.0.0.1"),
                    help="IP ou nome do servidor (variável LTP_SERVIDOR)")
    ap.add_argument("--porta", type=int, default=int(os.environ.get("LTP_PORTA", p.PORTA_PADRAO)))
    ap.add_argument("--unidade", default=os.environ.get("LTP_UNIDADE"),
                    help="unidade que está enviando (variável LTP_UNIDADE)")
    ap.add_argument("--token", default=os.environ.get("LTP_TOKEN"),
                    help="token da unidade (prefira a variável LTP_TOKEN)")
    ap.add_argument("--tls-ca", default=os.environ.get("LTP_TLS_CA"),
                    help="certificado da CA da clínica; liga o TLS (variável LTP_TLS_CA)")
    ap.add_argument("--tls-nome", default=os.environ.get("LTP_TLS_NOME", p.NOME_TLS_PADRAO),
                    help="nome esperado no certificado do servidor (padrão: servidor)")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--tentativas", type=int, default=5)
    ap.add_argument("--log", help="também grava o log neste arquivo")
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("ping", help="testa a comunicação com o servidor")
    env = sub.add_parser("enviar", help="envia um laudo para outra unidade")
    env.add_argument("arquivo")
    env.add_argument("--destino", required=True)
    env.add_argument("--limite", type=int, metavar="KB/s",
                     help="limita a taxa de envio (simula o link lento de uma unidade)")
    env.add_argument("--simular-queda", type=int, metavar="BYTES",
                     help="derruba a conexão após BYTES enviados (teste de retomada)")
    sub.add_parser("listar", help="lista os laudos recebidos pela unidade")
    baixa = sub.add_parser("baixar", help="baixa um laudo recebido, conferindo o SHA-256")
    baixa.add_argument("nome")
    baixa.add_argument("--pasta", default=os.environ.get("LTP_PASTA", "recebidos"),
                       help="pasta onde salvar (variável LTP_PASTA; padrão: recebidos)")
    baixa.add_argument("--simular-queda", type=int, metavar="BYTES",
                       help="derruba a conexão após BYTES recebidos (teste de retomada)")
    args = ap.parse_args()

    handlers = [logging.StreamHandler()]
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    if args.comando in ("enviar", "listar", "baixar") and not args.unidade:
        ap.error(f"o comando {args.comando} exige --unidade (ou LTP_UNIDADE)")

    try:
        tls = p.contexto_tls_cliente(args.tls_ca) if args.tls_ca else None
    except OSError as erro:
        ap.error(f"não foi possível ler a CA {args.tls_ca}: {erro}")
    cli = Cliente(args.servidor, args.porta, args.unidade, args.token,
                  args.timeout, args.tentativas, tls, args.tls_nome)
    try:
        if args.comando == "ping":
            resposta, rtt = cli.ping()
            canal = "com TLS" if tls else "sem TLS"
            print(f"{resposta['msg']} de {resposta['servidor']} ({resposta['versao']}, {canal}) "
                  f"em {rtt:.1f} ms, hora do servidor {resposta['hora']}")
        elif args.comando == "enviar":
            resposta = cli.enviar(args.arquivo, args.destino, args.simular_queda, args.limite)
            print(f"{resposta['msg']}: {resposta['arquivo']} sha256={resposta['sha256']}")
        elif args.comando == "baixar":
            resposta = cli.baixar(args.nome, args.pasta, args.simular_queda)
            print(f"BAIXADO: {resposta['arquivo']} ({resposta['tamanho']} bytes) "
                  f"sha256={resposta['sha256']} conferido")
        else:
            arquivos = cli.listar()
            print(f"{len(arquivos)} laudo(s) na entrada de {args.unidade}:")
            for a in arquivos:
                print(f"  {a['nome']:<40} {a['tamanho']:>12} B  {a['sha256']}")
    except ErroServidor as erro:
        log.error("servidor recusou: %s", erro)
        return 1
    except ErroIntegridade as erro:
        log.error("%s", erro)
        return 1
    except ssl.SSLCertVerificationError as erro:
        log.error("certificado do servidor recusado: %s", erro.verify_message or erro)
        return 1
    except (OSError, p.ErroProtocolo) as erro:
        dica = ""
        if tls is None and isinstance(erro, (ConnectionError, p.ErroProtocolo)):
            dica = " (se o servidor usa TLS, informe --tls-ca)"
        log.error("falha de comunicação: %s%s", erro, dica)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
