"""Programathor: vagas remotas de tecnologia no Brasil (HTML publico).

O site NAO tem busca por texto (o campo de busca so filtra cidade). O que existe sao paginas por tecnologia,
por exemplo /jobs-python e /jobs-selenium, e o filtro ?remoto=true. Por isso os "termos" desta fonte sao NOMES DE
TECNOLOGIA (python, selenium, n8n, robotframework, autoit...); um termo sem pagina no site (404) e ignorado.

O card traz titulo, empresa, modelo (Remoto), nivel e contrato (PJ/CLT) como texto visivel, mas nao traz a
data de publicacao (so o selo NOVA), entao a idade fica em branco (None) quando nao ha o selo.

Seguranca: ritmo lento, limite de descricoes por execucao e, se o site responder 429, a fonte e abandonada na hora
e entra em pausa (nao ha retry imediato).
"""
import random
import re
import time

import requests

from ..pontuacao import pontuar
from ..texto import html_para_texto, norm, slug

BASE_URL = "https://programathor.com.br"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def tipo_contrato(texto_visivel: str) -> str:
    """'PJ', 'CLT', 'CLT / PJ' ou 'estagio' a partir do texto VISIVEL do card (nao do HTML bruto)."""
    t = norm(texto_visivel)
    pj, clt = re.search(r"\bpj\b", t), re.search(r"\bclt\b", t)
    if pj and clt:
        return "CLT / PJ"
    if pj:
        return "PJ"
    if clt:
        return "CLT"
    return "estagio" if re.search(r"\bestagio\b", t) else ""


class Programathor:
    NOME = "programathor"
    ROTULO = "Programathor"

    def __init__(self, cfg: dict, log, parar, **_):
        self.cfg, self.log, self.parar = cfg, log, parar
        fc = cfg.get("fontes", {}).get("programathor", {})
        self.pausa_busca = fc.get("pausa_busca_seg", [5.0, 10.0])
        self.pausa_desc = fc.get("pausa_descricao_seg", [4.0, 8.0])
        self.max_desc = fc.get("max_descricoes_por_execucao", 5)
        self.baixadas = 0
        self.avisos: list[str] = []

    def __enter__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        return self

    def __exit__(self, *exc):
        self.s.close()

    def _get(self, url: str, params: dict | None = None, referer: str = BASE_URL) -> requests.Response:
        from . import Bloqueio

        r = self.s.get(url, params=params, headers={"Referer": referer}, timeout=25)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After", "")
            raise Bloqueio("Programathor limitou o acesso (HTTP 429)", int(ra) if ra.isdigit() else None)
        return r

    @staticmethod
    def _card(c: str):
        link = re.search(r'href="(/jobs/(\d+)-[^"]+)"', c)
        if not link:
            return None
        visivel = html_para_texto(re.sub(r"<script.*?</script>", "", c, flags=re.S | re.I))
        if "remoto" not in norm(visivel):
            return None
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", c, re.S)
        titulo = re.sub(r"\s*NOVA\s*$", "", html_para_texto(h3.group(1)) if h3 else "").strip()
        alt = re.search(r'alt="([^"]+)"', c)
        return {
            "id": f"prog:{link.group(2)}",
            "titulo": titulo,
            "empresa": alt.group(1).strip() if alt else "",
            "nota": None,
            "idade_dias": 0 if re.search(r"\bnova\b", norm(visivel)) else None,  # o site nao informa a data
            "local": "Remoto",
            "easy": 0,
            "link": f"{BASE_URL}{link.group(1)}",
            "tipo_raw": tipo_contrato(visivel),
        }

    def buscar(self, termo: str) -> list[dict]:
        if self.parar and self.parar.is_set():
            return []
        r = self._get(f"{BASE_URL}/jobs-{slug(termo)}", params={"remoto": "true"})
        if r.status_code == 404:
            self.log(f"  [Programathor] nao ha pagina para a tecnologia '{termo}' (use nomes de tecnologia)")
            return []
        if not r.ok:
            self.avisos.append(f"Programathor retornou HTTP {r.status_code}")
            return []
        vagas, vistos = [], set()
        for c in re.findall(r'<div class="cell-list\s*">(.*?)</a>\s*</div>', r.text, re.S):
            v = self._card(c)
            if v and v["id"] not in vistos:
                vistos.add(v["id"])
                vagas.append(v)
        time.sleep(random.uniform(*self.pausa_busca))
        return vagas

    def vale_descricao(self, v: dict) -> str:
        """Evita baixar descricoes de vagas irrelevantes e limita as requisicoes por execucao."""
        r = pontuar(self.cfg, v["titulo"], "")
        if r is None or r <= 0:
            return "nao"
        return "sim" if self.baixadas < self.max_desc else "depois"

    def descricao(self, v: dict) -> str:
        from . import Bloqueio

        self.baixadas += 1
        time.sleep(random.uniform(*self.pausa_desc))
        try:
            r = self._get(v["link"], referer=BASE_URL)
        except Bloqueio:
            raise  # 429: a fonte entra em pausa
        except Exception:
            return ""  # falha de rede isolada: so esta descricao fica vazia
        if not r.ok:
            return ""
        m = (re.search(r'<div class="wrapper-content-job-show">(.*?)<div class="wrapper-content-company-show"', r.text, re.S)
             or re.search(r'<div class="wrapper-content-job-show">(.*?)<footer', r.text, re.S))
        return html_para_texto(m.group(1)) if m else ""
