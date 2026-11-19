"""
Protocolo LTP/1 (Laudo Transfer Protocol), camada de aplicação do projeto.

Toda mensagem de controle tem o formato:

    [4 bytes: tamanho N do corpo, inteiro sem sinal big-endian][N bytes: JSON em UTF-8]

O prefixo de tamanho resolve o problema de "onde termina a mensagem" em um
fluxo TCP, que não preserva fronteiras entre envios.

Transferência de arquivos (bytes crus, sem JSON):
  * UPLOAD: depois que o servidor responde READY, o cliente envia os bytes do
    arquivo a partir do offset informado pelo servidor. O offset é o tamanho
    do arquivo parcial que o servidor já tem: é assim que a retomada funciona.
  * DOWNLOAD: o cliente informa quantos bytes já tem (offset); o servidor
    responde READY com tamanho e SHA-256 e manda os bytes a partir dali.

Opcionalmente, todo o canal passa por TLS (módulo ssl): o formato das
mensagens é o mesmo, só que cifrado.
"""

import hashlib
import json
import re
import ssl
import struct

VERSAO = "LTP/1"
PORTA_PADRAO = 5050
PORTA_HTTP_PADRAO = 8080           # painel e métricas (HTTP), só leitura
NOME_TLS_PADRAO = "servidor"       # nome que o certificado do servidor precisa ter
TAM_BLOCO = 64 * 1024              # leitura e escrita em blocos de 64 KiB
TAM_MAX_MENSAGEM = 64 * 1024       # impede que um cliente mande um JSON gigante
TAM_MAX_ARQUIVO = 2 * 1024 ** 3    # 2 GiB por laudo

_CABECALHO = struct.Struct("!I")   # "!" = ordem de rede (big-endian), "I" = uint32

# Nomes aceitos: letras, números, ponto, hífen e sublinhado, sem começar por
# ponto. Isso bloqueia "../", barras e arquivos ocultos (path traversal).
_RE_NOME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ErroProtocolo(Exception):
    """Mensagem recebida fora do formato do LTP/1."""


def receber_exato(sock, n):
    """Lê exatamente n bytes do socket. recv() pode devolver menos que o pedido."""
    partes = []
    faltam = n
    while faltam > 0:
        bloco = sock.recv(min(faltam, TAM_BLOCO))
        if not bloco:
            raise ConnectionError("conexão encerrada pelo outro lado")
        partes.append(bloco)
        faltam -= len(bloco)
    return b"".join(partes)


def enviar_msg(sock, dados):
    """Serializa o dicionário em JSON e envia com o prefixo de tamanho."""
    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    sock.sendall(_CABECALHO.pack(len(corpo)) + corpo)


def receber_msg(sock):
    """Recebe uma mensagem de controle e devolve o dicionário correspondente."""
    (n,) = _CABECALHO.unpack(receber_exato(sock, _CABECALHO.size))
    if n == 0 or n > TAM_MAX_MENSAGEM:
        raise ErroProtocolo(f"tamanho de mensagem inválido: {n} bytes")
    try:
        msg = json.loads(receber_exato(sock, n).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ErroProtocolo("corpo da mensagem não é JSON válido") from erro
    if not isinstance(msg, dict):
        raise ErroProtocolo("a mensagem deve ser um objeto JSON")
    return msg


def sha256_arquivo(caminho):
    """SHA-256 do arquivo inteiro, lido em blocos (não carrega tudo na memória)."""
    with open(caminho, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def nome_valido(nome):
    return isinstance(nome, str) and bool(_RE_NOME.fullmatch(nome))


def sha256_valido(valor):
    return isinstance(valor, str) and bool(_RE_SHA256.fullmatch(valor))


def tamanho_valido(valor):
    # bool é subclasse de int em Python, por isso a checagem extra
    return (isinstance(valor, int) and not isinstance(valor, bool)
            and 0 <= valor <= TAM_MAX_ARQUIVO)


# ================================================================== TLS

def contexto_tls_servidor(certificado, chave):
    """Contexto TLS do servidor: certificado assinado pela CA da clínica, TLS 1.2+."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certificado, chave)
    return ctx


def contexto_tls_cliente(ca):
    """
    Contexto TLS do cliente: confia só na CA da clínica (arquivo ca), exige
    certificado válido e confere se o nome no certificado é o esperado.
    """
    ctx = ssl.create_default_context(cafile=ca)  # CERT_REQUIRED e check_hostname
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
