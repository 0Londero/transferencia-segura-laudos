"""
Monitoramento do servidor: contadores e painel HTTP (requisito 5.6 do
enunciado, protocolo de aplicação padronizado).

O painel roda em uma thread separada do servidor LTP/1 e só responde GET:

    /          painel HTML para a TI da clínica (atualiza sozinho a cada 5 s)
    /saude     JSON curto para verificação de disponibilidade (HTTP 200 ou 503)
    /metricas  JSON com contadores, recursos do processo e laudos por unidade

Ele não mostra o conteúdo dos laudos e não permite alterar nada. O acesso à
porta é limitado pelo firewall (ufw) à rede da TI.
"""

import html
import json
import logging
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

log = logging.getLogger("http")


class Metricas:
    """Contadores do servidor. Várias threads atualizam ao mesmo tempo, por isso o Lock."""

    CAMPOS = ("conexoes_ativas", "conexoes_total", "conexoes_recusadas_lotado",
              "falhas_tls", "autenticacoes_recusadas", "uploads_ok", "uploads_retomados",
              "uploads_interrompidos", "uploads_hash_divergente", "bytes_recebidos",
              "downloads_ok", "downloads_interrompidos", "bytes_enviados",
              "requisicoes_http")

    def __init__(self):
        self._trava = threading.Lock()
        self._contadores = dict.fromkeys(self.CAMPOS, 0)
        self._eventos = deque(maxlen=12)  # só os mais recentes, memória constante
        self.inicio = time.time()

    def somar(self, campo, n=1):
        with self._trava:
            self._contadores[campo] += n

    def evento(self, texto):
        with self._trava:
            self._eventos.appendleft((time.strftime("%H:%M:%S"), texto))

    def retrato(self):
        """Cópia consistente dos contadores e eventos (lida de uma vez, sob o Lock)."""
        with self._trava:
            return dict(self._contadores), list(self._eventos)


def iniciar_http(servidor, host, porta):
    """Sobe o servidor HTTP em uma thread daemon e devolve o objeto para o shutdown()."""

    class Tratador(_Tratador):
        fonte = servidor

    httpd = ThreadingHTTPServer((host, porta), Tratador)
    httpd.daemon_threads = True  # uma thread por requisição, encerradas com o processo
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    return httpd


class _Tratador(BaseHTTPRequestHandler):
    server_version = "CentralLaudos/1.0"
    sys_version = ""   # não divulga a versão do Python no cabeçalho Server
    timeout = 10       # cliente HTTP parado não segura a thread para sempre
    # Cabeçalho e corpo saem em duas escritas; com o Nagle ligado, o corpo
    # esperava o ACK atrasado do navegador (cerca de 40 ms por requisição)
    disable_nagle_algorithm = True
    fonte = None       # o objeto Servidor (definido em iniciar_http)

    def do_GET(self):
        self._responder(com_corpo=True)

    def do_HEAD(self):
        self._responder(com_corpo=False)

    def _responder(self, com_corpo):
        self.fonte.metricas.somar("requisicoes_http")
        rota = urlsplit(self.path).path
        if rota == "/saude":
            dados = self.fonte.saude()
            self._enviar_json(200 if dados["status"] == "ok" else 503, dados, com_corpo)
        elif rota == "/metricas":
            self._enviar_json(200, self.fonte.estado(), com_corpo)
        elif rota == "/":
            corpo = painel_html(self.fonte.estado()).encode("utf-8")
            self._enviar(200, "text/html; charset=utf-8", corpo, com_corpo)
        else:
            self._enviar_json(404, {"erro": "rota inexistente",
                                    "rotas": ["/", "/saude", "/metricas"]}, com_corpo)

    def _enviar_json(self, codigo, dados, com_corpo):
        corpo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
        self._enviar(codigo, "application/json; charset=utf-8", corpo, com_corpo)

    def _enviar(self, codigo, tipo, corpo, com_corpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if com_corpo:
            self.wfile.write(corpo)

    def log_request(self, code="-", size="-"):
        # O painel se atualiza a cada 5 s; para não poluir o log, só /saude e
        # /metricas (usados por scripts) e os erros aparecem no nível INFO
        codigo = int(code) if str(code).isdigit() else 0
        nivel = logging.INFO if (codigo >= 400 or self.path != "/") else logging.DEBUG
        log.log(nivel, "HTTP %s %s %s de %s", self.command, self.path, codigo,
                self.client_address[0])

    def log_message(self, formato, *args):
        # Chamado pelo http.server em erros (requisição malformada, timeout)
        log.warning("HTTP %s: %s", self.client_address[0], formato % args)


# ===================================================================== painel

def _bytes(n):
    """Tamanho em unidades decimais (1 MB = 10^6 bytes), as mesmas do log do servidor."""
    for unidade in ("B", "KB", "MB"):
        if n < 1000:
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1000
    return f"{n:.1f} GB"


def _tempo(segundos):
    s = int(segundos)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 86400} d {s % 86400 // 3600} h"


def painel_html(e):
    """Monta o painel a partir do mesmo dicionário servido em /metricas."""
    c, proc = e["contadores"], e["processo"]
    esc = html.escape
    cartoes = [
        ("Conexões ativas", f"{c['conexoes_ativas']} / {e['limite_clientes']}"),
        ("Laudos recebidos", c["uploads_ok"]),
        ("Retomadas", c["uploads_retomados"]),
        ("Hash divergente", c["uploads_hash_divergente"]),
        ("Downloads", c["downloads_ok"]),
        ("Autenticações recusadas", c["autenticacoes_recusadas"]),
    ]
    linhas_unidades = "".join(
        f"<tr><td>{esc(nome)}</td><td>{u['laudos']}</td><td>{_bytes(u['bytes'])}</td>"
        f"<td>{u['parciais']}</td></tr>" for nome, u in e["unidades"].items())
    linhas_eventos = "".join(
        f"<li><span>{esc(hora)}</span> {esc(texto)}</li>" for hora, texto in e["eventos"]
    ) or "<li>nenhum evento ainda</li>"
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Central de laudos</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f4f6f8; color: #1d2430; }}
 h1 {{ font-size: 26px; margin: 0 0 4px; }}
 .sub {{ color: #556; margin-bottom: 18px; }}
 .ok {{ color: #fff; background: #1f8a4c; padding: 2px 10px; border-radius: 10px; }}
 .grade {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
 .cartao {{ background: #fff; border-radius: 8px; padding: 14px; box-shadow: 0 1px 3px #0002; }}
 .cartao b {{ display: block; font-size: 30px; }}
 .cartao span {{ color: #556; font-size: 14px; }}
 section {{ background: #fff; border-radius: 8px; padding: 14px; margin-top: 16px; box-shadow: 0 1px 3px #0002; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 16px; }}
 th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #e3e6ea; }}
 ul {{ list-style: none; padding: 0; margin: 0; font-size: 15px; }}
 li {{ padding: 4px 0; border-bottom: 1px solid #eef0f2; }}
 li span {{ color: #556; font-family: monospace; margin-right: 8px; }}
 .duas {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
 @media (max-width: 800px) {{ .duas {{ grid-template-columns: 1fr; }} }}
</style></head><body>
<h1>Central de laudos <span class="ok">no ar</span></h1>
<div class="sub">{esc(e['servidor'])} | {esc(e['versao'])} na porta {e['porta']}/TCP
 {'com TLS' if e['tls'] else 'sem TLS'} | no ar há {_tempo(e['uptime_s'])}
 | PID {proc['pid']}, {proc['threads']} threads, {_bytes(proc['memoria_rss_kb'] * 1024)} de RAM
 | atualiza a cada 5 s</div>
<div class="grade">{''.join(f'<div class="cartao"><b>{v}</b><span>{esc(t)}</span></div>' for t, v in cartoes)}</div>
<div class="duas">
<section><h2>Laudos por unidade</h2><table>
<tr><th>Unidade</th><th>Laudos</th><th>Volume</th><th>Envios parciais</th></tr>
{linhas_unidades}</table>
<p>Recebido: {_bytes(c['bytes_recebidos'])} | enviado: {_bytes(c['bytes_enviados'])}
 | disco livre: {e['disco']['livre_gb']} GB</p></section>
<section><h2>Eventos recentes</h2><ul>{linhas_eventos}</ul></section>
</div>
</body></html>"""
