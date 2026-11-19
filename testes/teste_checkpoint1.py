"""
REGISTRO HISTÓRICO do Checkpoint 1 (servidor sem TLS). A suíte atual, com
TLS, download, HTTP e carga, é testes/teste_sistema.py.

Testes do Checkpoint 1: comunicação cliente/servidor, concorrência, retomada,
integridade e tratamento de falhas. Precisa do servidor rodando.

Uso:
    python testes/teste_checkpoint1.py --servidor 172.19.114.51

Cada teste imprime OK ou FALHOU; o código de saída é 0 só se todos passarem.
"""

import argparse
import hashlib
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import protocolo as p  # noqa: E402
from cliente import Cliente, ErroServidor  # noqa: E402

MB = 1024 * 1024
TOKENS = {u: f"token-{u}-2026" for u in ("centro", "batel", "agua-verde")}
resultados = []


def registrar(tid, descricao, passou, detalhe):
    resultados.append((tid, passou))
    print(f"[{'OK' if passou else 'FALHOU':6}] {tid} {descricao}: {detalhe}", flush=True)


def criar_laudo(pasta, nome, tamanho):
    caminho = Path(pasta) / nome
    caminho.write_bytes(os.urandom(tamanho))
    return caminho


def cliente(args, unidade=None, **extra):
    return Cliente(args.servidor, args.porta, unidade, TOKENS.get(unidade), **extra)


def t01_ping(args):
    resposta, rtt = cliente(args).ping()
    registrar("T01", "primeira comunicação (PING)", resposta["msg"] == "PONG",
              f"{resposta['msg']} de {resposta['servidor']} em {rtt:.1f} ms")


def t02_concorrencia(args, pasta, sufixo):
    rotas = [("centro", "batel"), ("batel", "agua-verde"), ("agua-verde", "centro")]
    saidas = {}

    def enviar(origem, destino):
        laudo = criar_laudo(pasta, f"laudo-{origem}-{sufixo}.bin", 20 * MB)
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
              f"total {total:.2f}s ({tempos})")


def t03_retomada(args, pasta, sufixo):
    laudo = criar_laudo(pasta, f"laudo-retomada-{sufixo}.bin", 10 * MB)
    queda = 4 * MB
    resposta = cliente(args, "batel").enviar(laudo, "centro", simular_queda=queda)
    offset = resposta.get("offset_inicial", 0)
    registrar("T03", "queda no meio do envio e retomada", resposta["msg"] == "RECEBIDO"
              and offset == queda, f"reconectou e retomou no byte {offset} de {10 * MB}")


def t04_hash_divergente(args, pasta, sufixo):
    laudo = criar_laudo(pasta, f"laudo-corrompido-{sufixo}.bin", 1 * MB)
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


def t05_token_errado(args):
    cli = Cliente(args.servidor, args.porta, "centro", "token-errado")
    try:
        cli.ping()
        registrar("T05", "token inválido é recusado", False, "servidor aceitou")
    except ErroServidor as erro:
        registrar("T05", "token inválido é recusado", erro.codigo == "AUTH_INVALIDA", str(erro))


def t06_path_traversal(args):
    with cliente(args, "centro").conectar() as sock:
        p.enviar_msg(sock, {"cmd": "UPLOAD", "nome": "../../etc/passwd", "destino": "batel",
                            "tamanho": 10, "sha256": "0" * 64})
        resposta = p.receber_msg(sock)
    registrar("T06", "nome com ../ é recusado", resposta.get("erro") == "NOME_INVALIDO",
              f"{resposta.get('erro')}: {resposta.get('detalhe')}")


def t07_servidor_fora(args):
    porta_fechada = args.porta + 1
    cli = Cliente(args.servidor, porta_fechada, timeout=3, tentativas=3)
    inicio = time.perf_counter()
    try:
        cli.ping()
        registrar("T07", "servidor fora do ar", False, "conectou numa porta sem serviço")
    except OSError as erro:
        registrar("T07", "servidor fora do ar: erro tratado após reconexões", True,
                  f"{type(erro).__name__} após 3 tentativas em {time.perf_counter() - inicio:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--servidor", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=p.PORTA_PADRAO)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s")

    print(f"Cliente {socket.gethostname()} -> servidor {args.servidor}:{args.porta}\n")
    sufixo = time.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory() as pasta:
        for teste in (t01_ping, t02_concorrencia, t03_retomada, t04_hash_divergente,
                      t05_token_errado, t06_path_traversal, t07_servidor_fora):
            precisa_pasta = teste.__code__.co_argcount == 3
            try:
                teste(args, pasta, sufixo) if precisa_pasta else teste(args)
            except Exception as erro:
                registrar(teste.__name__[:3].upper(), teste.__name__, False, repr(erro))
    aprovados = sum(1 for _, ok in resultados if ok)
    print(f"\n{aprovados}/{len(resultados)} testes aprovados")
    return 0 if aprovados == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
