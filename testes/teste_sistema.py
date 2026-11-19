"""
Testes de sistema do projeto (T01 a T14): comunicação, concorrência, retomada,
integridade, autenticação, validação de entrada, falhas de rede, download,
isolamento entre unidades, HTTP, TLS, limite de conexões e carga.
Precisa do servidor rodando.

Uso:
    python testes/teste_sistema.py --servidor 172.19.114.51 --tls-ca certs/ca.crt
    python testes/teste_sistema.py --servidor 127.0.0.1                 (servidor sem TLS)
    python testes/teste_sistema.py --http-porta 0                       (sem acesso ao painel)
    python testes/teste_sistema.py --tls-ca certs/ca.crt --somente T01,T08

Cada teste imprime OK, FALHOU ou PULADO; o código de saída é 0 só se nenhum falhar.
"""

import argparse
import hashlib
import json
import logging
import os
import socket
import ssl
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import protocolo as p  # noqa: E402
from cliente import Cliente, ErroServidor  # noqa: E402

MB = 1024 * 1024
UNIDADES = ("centro", "batel", "agua-verde")
TOKENS = {u: f"token-{u}-2026" for u in UNIDADES}
resultados = []


def registrar(tid, descricao, situacao, detalhe):
    """situacao: True (OK), False (FALHOU) ou None (PULADO)."""
    rotulo = {True: "OK", False: "FALHOU", None: "PULADO"}[situacao]
    resultados.append((tid, situacao))
    print(f"[{rotulo:6}] {tid} {descricao}: {detalhe}", flush=True)


def criar_laudo(pasta, nome, tamanho):
    caminho = Path(pasta) / nome
    caminho.write_bytes(os.urandom(tamanho))
    return caminho


def cliente(args, unidade=None, **extra):
    extra.setdefault("tls", args.tls)
    return Cliente(args.servidor, args.porta, unidade, TOKENS.get(unidade),
                   tls_nome=args.tls_nome, **extra)


def http_get(args, rota):
    """GET no painel HTTP; devolve (código, corpo JSON ou None, tempo em ms)."""
    url = f"http://{args.servidor}:{args.http_porta}{rota}"
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            codigo, corpo = r.status, r.read()
    except urllib.error.HTTPError as erro:
        codigo, corpo = erro.code, erro.read()
    ms = (time.perf_counter() - inicio) * 1000
    try:
        return codigo, json.loads(corpo), ms
    except ValueError:
        return codigo, None, ms


def metricas(args):
    if not args.http_porta:
        return None
    try:
        return http_get(args, "/metricas")[1]
    except OSError:
        return None


# ========================================================================= testes

def t01_ping(args, ctx):
    rtts = []
    for _ in range(10):
        resposta, rtt = cliente(args).ping()
        rtts.append(rtt)
    registrar("T01", "comunicação (10 PINGs)", resposta["msg"] == "PONG",
              f"PONG de {resposta['servidor']} ({'TLS' if resposta.get('tls') else 'sem TLS'}); "
              f"tempo de resposta médio {statistics.mean(rtts):.1f} ms "
              f"(mín. {min(rtts):.1f}, máx. {max(rtts):.1f})")


def t02_concorrencia(args, ctx):
    rotas = [("centro", "batel"), ("batel", "agua-verde"), ("agua-verde", "centro")]
    saidas = {}

    def enviar(origem, destino):
        laudo = criar_laudo(ctx["pasta"], f"laudo-{origem}-{ctx['sufixo']}.bin", 20 * MB)
        ctx[f"sha-{origem}"] = p.sha256_arquivo(laudo)
        inicio = time.perf_counter()
        try:
            saidas[origem] = (cliente(args, origem).enviar(laudo, destino),
                              time.perf_counter() - inicio)
        except Exception as erro:  # registra a falha sem derrubar as outras threads
            saidas[origem] = (erro, 0)

    threads = [threading.Thread(target=enviar, args=r) for r in rotas]
    inicio = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - inicio
    ok = all(isinstance(r, dict) and r.get("msg") == "RECEBIDO" for r, _ in saidas.values())
    tempos = ", ".join(f"{o} {d:.2f}s" for o, (_, d) in sorted(saidas.items()))
    registrar("T02", "3 unidades enviando 20 MB ao mesmo tempo", ok,
              f"total {total:.2f}s ({tempos}); vazão agregada {60 / total:.0f} MB/s")


def t03_retomada(args, ctx):
    laudo = criar_laudo(ctx["pasta"], f"laudo-retomada-{ctx['sufixo']}.bin", 10 * MB)
    ctx["sha-retomada"] = p.sha256_arquivo(laudo)
    queda = 4 * MB
    resposta = cliente(args, "batel").enviar(laudo, "centro", simular_queda=queda)
    offset = resposta.get("offset_inicial", 0)
    registrar("T03", "queda no meio do envio e retomada", resposta["msg"] == "RECEBIDO"
              and offset == queda, f"reconectou e retomou no byte {offset} de {10 * MB}")


def t04_hash_divergente(args, ctx):
    laudo = criar_laudo(ctx["pasta"], f"laudo-corrompido-{ctx['sufixo']}.bin", 1 * MB)
    hash_falso = hashlib.sha256(b"outro conteudo").hexdigest()
    with cliente(args, "centro").conectar() as sock:
        p.enviar_msg(sock, {"cmd": "UPLOAD", "nome": laudo.name, "destino": "batel",
                            "tamanho": laudo.stat().st_size, "sha256": hash_falso})
        offset = p.receber_msg(sock)["offset"]
        with open(laudo, "rb") as f:
            sock.sendfile(f, offset)
        resposta = p.receber_msg(sock)
    registrar("T04", "servidor rejeita arquivo com SHA-256 divergente",
              resposta.get("erro") == "HASH_DIVERGENTE",
              f"{resposta.get('erro')} (calculado {resposta.get('calculado', '')[:16]}...)")


def t05_token_errado(args, ctx):
    cli = Cliente(args.servidor, args.porta, "centro", "token-errado",
                  tls=args.tls, tls_nome=args.tls_nome)
    inicio = time.perf_counter()
    try:
        cli.ping()
        registrar("T05", "token inválido é recusado", False, "servidor aceitou")
    except ErroServidor as erro:
        registrar("T05", "token inválido é recusado", erro.codigo == "AUTH_INVALIDA",
                  f"{erro} (resposta em {time.perf_counter() - inicio:.1f}s)")


def t06_path_traversal(args, ctx):
    with cliente(args, "centro").conectar() as sock:
        p.enviar_msg(sock, {"cmd": "UPLOAD", "nome": "../../etc/passwd", "destino": "batel",
                            "tamanho": 10, "sha256": "0" * 64})
        resposta = p.receber_msg(sock)
    registrar("T06", "nome com ../ é recusado", resposta.get("erro") == "NOME_INVALIDO",
              f"{resposta.get('erro')}: {resposta.get('detalhe')}")


def t07_servidor_fora(args, ctx):
    porta_fechada = args.porta + 1
    cli = Cliente(args.servidor, porta_fechada, timeout=3, tentativas=3)
    inicio = time.perf_counter()
    try:
        cli.ping()
        registrar("T07", "servidor fora do ar", False, "conectou numa porta sem serviço")
    except OSError as erro:
        registrar("T07", "servidor fora do ar: erro tratado após reconexões", True,
                  f"{type(erro).__name__} após 3 tentativas em {time.perf_counter() - inicio:.1f}s")


def t08_download(args, ctx):
    # batel baixa o laudo que centro mandou para ela no T02
    nome = f"laudo-centro-{ctx['sufixo']}.bin"
    resposta = cliente(args, "batel").baixar(nome, Path(ctx["pasta"]) / "batel")
    ok = resposta["sha256"] == ctx.get("sha-centro") == p.sha256_arquivo(resposta["arquivo"])
    registrar("T08", "unidade de destino baixa o laudo e confere o SHA-256", ok,
              f"{resposta['tamanho']} bytes, SHA-256 igual ao do arquivo original")


def t09_retomada_download(args, ctx):
    nome = f"laudo-retomada-{ctx['sufixo']}.bin"   # enviado para centro no T03
    queda = 3 * MB
    resposta = cliente(args, "centro").baixar(nome, Path(ctx["pasta"]) / "centro",
                                              simular_queda=queda)
    offset = resposta["offset_inicial"]
    ok = queda <= offset < 10 * MB and resposta["sha256"] == ctx.get("sha-retomada")
    registrar("T09", "queda no meio do download e retomada", ok,
              f"retomou no byte {offset} de {10 * MB}; SHA-256 confere")


def t10_isolamento(args, ctx):
    # laudo-batel-* está na entrada de agua-verde; centro não pode baixá-lo
    nome = f"laudo-batel-{ctx['sufixo']}.bin"
    try:
        cliente(args, "centro").baixar(nome, Path(ctx["pasta"]) / "intruso")
        registrar("T10", "unidade não acessa a entrada de outra", False, "download permitido")
    except ErroServidor as erro:
        visiveis = [a["nome"] for a in cliente(args, "centro").listar()]
        registrar("T10", "unidade não acessa a entrada de outra",
                  erro.codigo == "NAO_ENCONTRADO" and nome not in visiveis,
                  f"{erro.codigo}; o laudo também não aparece no LIST de centro")


def t11_http(args, ctx):
    if not args.http_porta:
        return registrar("T11", "painel HTTP", None, "desligado com --http-porta 0")
    cod_saude, saude, ms = http_get(args, "/saude")
    cod_met, met, _ = http_get(args, "/metricas")
    cod_404, _, _ = http_get(args, "/nao-existe")
    ok = (cod_saude, cod_met, cod_404) == (200, 200, 404) and saude["status"] == "ok"
    c = met["contadores"] if met else {}
    registrar("T11", "HTTP: /saude, /metricas e rota inexistente", ok,
              f"/saude {cod_saude} em {ms:.1f} ms, /metricas {cod_met} "
              f"({c.get('uploads_ok')} laudos recebidos, {met['processo']['threads']} threads), "
              f"/nao-existe {cod_404}")


def t12_tls(args, ctx):
    if args.tls is None:
        return registrar("T12", "TLS", None, "servidor testado sem TLS (informe --tls-ca)")
    falhas = []
    # a) cliente sem TLS contra servidor com TLS
    try:
        Cliente(args.servidor, args.porta, tentativas=1).ping()
        falhas.append("cliente sem TLS foi atendido")
    except (OSError, p.ErroProtocolo) as erro:
        sem_tls = type(erro).__name__
    # b) cliente que não confia na CA da clínica (só nas CAs públicas do sistema)
    try:
        Cliente(args.servidor, args.porta, tentativas=1, tls=ssl.create_default_context()).ping()
        falhas.append("certificado aceito sem a CA da clínica")
    except ssl.SSLCertVerificationError as erro:
        sem_ca = erro.verify_message
    # c) nome diferente do certificado (servidor falso com certificado de outro nome)
    try:
        Cliente(args.servidor, args.porta, tentativas=1, tls=args.tls,
                tls_nome="impostor").ping()
        falhas.append("nome errado aceito")
    except ssl.SSLCertVerificationError as erro:
        nome_errado = erro.verify_message
    if falhas:
        return registrar("T12", "TLS recusa canal inseguro", False, "; ".join(falhas))
    registrar("T12", "TLS recusa canal inseguro", True,
              f"sem TLS: {sem_tls}; sem a CA: {sem_ca}; nome errado: {nome_errado}")


def t13_limite_conexoes(args, ctx):
    antes = metricas(args)
    limite = antes["limite_clientes"] if antes else args.max_clientes
    abertas = []
    try:
        for _ in range(limite):
            sock = cliente(args).conectar()
            p.enviar_msg(sock, {"cmd": "PING"})
            p.receber_msg(sock)          # garante que a conexão já tem thread no servidor
            abertas.append(sock)
        try:
            cliente(args, tentativas=1).ping()
            recusada = "aceita"
        except ErroServidor as erro:
            recusada = erro.codigo       # sem TLS: o servidor responde LOTADO
        except OSError as erro:
            recusada = type(erro).__name__  # com TLS: fecha antes do handshake
    finally:
        for sock in abertas:
            sock.close()
    depois = metricas(args)
    extra = ""
    if antes and depois:
        extra = (f"; recusas no /metricas: {antes['contadores']['conexoes_recusadas_lotado']}"
                 f" -> {depois['contadores']['conexoes_recusadas_lotado']}")
    registrar("T13", f"limite de {limite} conexões simultâneas", recusada != "aceita",
              f"{len(abertas)} conexões abertas; a seguinte foi recusada ({recusada}){extra}")
    time.sleep(1)  # dá tempo de o servidor liberar as vagas antes do próximo teste


def t14_carga(args, ctx):
    n, tamanho = 15, 4 * MB
    erros, tempos = [], []

    def enviar(i):
        origem, destino = UNIDADES[i % 3], UNIDADES[(i + 1) % 3]
        laudo = criar_laudo(ctx["pasta"], f"carga-{i:02d}-{ctx['sufixo']}.bin", tamanho)
        inicio = time.perf_counter()
        try:
            cliente(args, origem).enviar(laudo, destino)
            tempos.append(time.perf_counter() - inicio)
        except Exception as erro:
            erros.append(repr(erro))

    threads = [threading.Thread(target=enviar, args=(i,)) for i in range(n)]
    inicio = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - inicio
    met = metricas(args)
    extra = f"; servidor com {met['processo']['memoria_rss_kb'] // 1024} MB de RAM" if met else ""
    registrar("T14", f"carga: {n} envios simultâneos de {tamanho // MB} MB", not erros,
              f"{len(tempos)}/{n} concluídos em {total:.2f}s; vazão agregada "
              f"{n * tamanho / MB / total:.0f} MB/s; tempo médio por envio "
              f"{statistics.mean(tempos) if tempos else 0:.2f}s{extra}"
              + (f"; erros: {erros[:2]}" if erros else ""))


TESTES = (t01_ping, t02_concorrencia, t03_retomada, t04_hash_divergente, t05_token_errado,
          t06_path_traversal, t07_servidor_fora, t08_download, t09_retomada_download,
          t10_isolamento, t11_http, t12_tls, t13_limite_conexoes, t14_carga)


def main():
    ap = argparse.ArgumentParser(description="Testes de sistema do LTP/1")
    ap.add_argument("--servidor", default=os.environ.get("LTP_SERVIDOR", "127.0.0.1"))
    ap.add_argument("--porta", type=int, default=p.PORTA_PADRAO)
    ap.add_argument("--tls-ca", default=os.environ.get("LTP_TLS_CA"),
                    help="CA da clínica (servidor com TLS)")
    ap.add_argument("--tls-nome", default=os.environ.get("LTP_TLS_NOME", p.NOME_TLS_PADRAO))
    ap.add_argument("--http-porta", type=int, default=p.PORTA_HTTP_PADRAO,
                    help="porta do painel HTTP (0 pula os testes que dependem dele)")
    ap.add_argument("--max-clientes", type=int, default=20,
                    help="limite do servidor, se o /metricas não estiver acessível")
    ap.add_argument("--somente", help="lista de testes, ex.: T01,T08 (dependências: "
                    "T08 precisa do T02; T09 precisa do T03; T10 precisa do T02)")
    ap.add_argument("-v", "--verboso", action="store_true", help="mostra o log do cliente")
    args = ap.parse_args()
    args.tls = p.contexto_tls_cliente(args.tls_ca) if args.tls_ca else None
    logging.basicConfig(level=logging.INFO if args.verboso else logging.ERROR,
                        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s")

    escolhidos = {t.strip().upper() for t in args.somente.split(",")} if args.somente else None
    canal = f"TLS (CA {args.tls_ca})" if args.tls else "sem TLS"
    print(f"Cliente {socket.gethostname()} -> servidor {args.servidor}:{args.porta}, {canal}\n")
    with tempfile.TemporaryDirectory() as pasta:
        ctx = {"pasta": pasta, "sufixo": time.strftime("%Y%m%d-%H%M%S")}
        for teste in TESTES:
            tid = teste.__name__[:3].upper()
            if escolhidos and tid not in escolhidos:
                continue
            try:
                teste(args, ctx)
            except Exception as erro:
                registrar(tid, teste.__name__[4:], False, repr(erro))
    aprovados = sum(1 for _, ok in resultados if ok)
    falhas = sum(1 for _, ok in resultados if ok is False)
    pulados = sum(1 for _, ok in resultados if ok is None)
    print(f"\n{aprovados} aprovados, {falhas} falharam, {pulados} pulados "
          f"(de {len(resultados)} testes)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
