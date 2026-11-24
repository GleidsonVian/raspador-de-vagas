"""LinkedIn: SOMENTE a busca publica de vagas (sem login, nunca com a sua conta).

Dois modos (config: fontes.linkedin.modo):
  - "navegador" (padrao): o Edge real do raspador (perfil proprio), como no Glassdoor. O LinkedIn fica atras do
    Cloudflare, que serve um desafio anti-robo a clientes HTTP simples (o `requests` leva 429 com JavaScript ofuscado,
    mesmo sem volume). Num navegador de verdade a pagina publica abre normalmente; se aparecer uma verificacao,
    VOCE resolve na janela e o raspador espera. Se o LinkedIn pedir login (authwall), a fonte e abandonada:
    o projeto nunca entra na sua conta.
  - "http": o endpoint publico "jobs-guest" via `requests` (cabecalhos simples, sem imitar navegador).
    Costuma tomar o desafio do Cloudflare; fica como alternativa e para testes.

Regras de seguranca (o LinkedIn proibe automacao e suspende contas):
  - nunca usa a sua conta; so paginas e endpoints publicos;
  - volume baixo: poucos termos, 1 pagina, pausas longas, descricao so de titulos relevantes (maximo por execucao);
  - bloqueio/verificacao nao resolvida/pedido de login = PARAR na hora (sem retry) e a fonte entra em pausa.
"""
import random
import re
import time
from datetime import date
from urllib.parse import urlencode

import requests

from .. import navegador
from ..pontuacao import pontuar
from ..texto import html_para_texto

BUSCA = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETALHE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
PAGINA_BUSCA = "https://www.linkedin.com/jobs/search/"
GEO_BRASIL = "106057199"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

_JS_DESCRICAO = """async (id) => {
  const r = await fetch('/jobs-guest/jobs/api/jobPosting/' + id, {credentials: 'include'});
  return [r.status, r.ok ? await r.text() : ''];
}"""


class LinkedIn:
    NOME = "linkedin"
    ROTULO = "LinkedIn"

    def __init__(self, cfg: dict, log, parar, manter_aberto: bool = False, **_):
        self.cfg, self.log, self.parar, self.manter = cfg, log, parar, manter_aberto
        fc = cfg.get("fontes", {}).get("linkedin", {})
        self.modo = fc.get("modo", "navegador")
        self.max_paginas = fc.get("max_paginas", 1)
        self.dias = cfg.get("apenas_ultimos_dias", 30)
        self.pausa_busca = fc.get("pausa_busca_seg", [25, 45])
        self.pausa_desc = fc.get("pausa_descricao_seg", [15, 28])
        self.max_desc = fc.get("max_descricoes_por_execucao", 6)
        self.espera_verificacao = fc.get("espera_verificacao_seg", 300)
        self.baixadas = 0
        self.avisos: list[str] = []

    # ------------------------------------------------------------------ ciclo de vida
    def __enter__(self):
        if self.modo == "navegador":
            self.pw, self.br, self.ctx, self.page = navegador.abrir()
        else:
            self.s = requests.Session()
            self.s.headers.update(HEADERS)
        return self

    def __exit__(self, *exc):
        if self.modo == "navegador":
            navegador.fechar(self.pw, self.br, self.ctx, self.page, self.manter)
        else:
            self.s.close()

    # ------------------------------------------------------------------ modo http
    def _get(self, url, referer: str = "https://www.linkedin.com/jobs/search", **kw):
        from . import Bloqueio

        r = self.s.get(url, timeout=30, headers={"referer": referer}, **kw)
        if r.status_code in (429, 999) or "authwall" in r.url or "/login" in r.url:
            ra = r.headers.get("Retry-After", "")
            # o 429 costuma vir do Cloudflare (pagina de desafio em JavaScript), decidido pelo tipo de cliente
            # e nao pelo nosso volume: esperar mais ou pedir menos nao resolve
            desafio = r.headers.get("Server", "").lower() == "cloudflare" and (
                "__cf_bm" in r.headers.get("Set-Cookie", "") or "_0x" in (r.text or "")[:3000])
            motivo = (f"LinkedIn serviu o desafio anti-robô do Cloudflare (HTTP {r.status_code}); não é limite de volume"
                      if desafio else f"LinkedIn limitou o acesso (HTTP {r.status_code})")
            raise Bloqueio(motivo, int(ra) if ra.isdigit() else None)
        r.raise_for_status()
        return r.text

    # ------------------------------------------------------------------ modo navegador
    def _estado(self, status: int | None = None) -> str:
        """'ok', 'desafio' (verificacao anti-robo) ou 'authwall' (pede login), olhando a pagina aberta."""
        try:
            url, titulo = self.page.url or "", (self.page.title() or "").lower()
        except Exception:  # navegando (ex.: verificacao acabou de ser resolvida)
            time.sleep(1.5)
            return "desafio"
        if "authwall" in url or "/login" in url or "/uas/" in url or "checkpoint" in url:
            return "authwall"
        if status in (429, 999) or any(t in titulo for t in ("security", "just a moment", "um momento", "verificando", "attention required")):
            return "desafio"
        return "ok"

    def _buscar_navegador(self, termo: str) -> list[dict]:
        from . import Bloqueio

        url = PAGINA_BUSCA + "?" + urlencode({"keywords": termo, "location": "Brasil", "geoId": GEO_BRASIL,
                                              "f_WT": "2", "f_TPR": f"r{self.dias * 86400}", "position": 1, "pageNum": 0})
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        estado = self._estado(getattr(resp, "status", None))
        if estado == "desafio":
            if not navegador.esperar_liberacao(lambda: self._estado() != "ok", self.log, self.parar, "LinkedIn", self.espera_verificacao):
                raise Bloqueio("LinkedIn pediu verificação anti-robô e ela não foi resolvida a tempo")
            estado = self._estado()
        if estado == "authwall":
            raise Bloqueio("LinkedIn pediu login (authwall); o projeto nunca entra na sua conta")
        try:
            self.page.wait_for_selector("ul.jobs-search__results-list li", timeout=15000)
        except Exception:
            pass
        return self._ler_cards(self.page.content())

    def _descricao_navegador(self, v: dict) -> str:
        from . import Bloqueio

        status, texto = self.page.evaluate(_JS_DESCRICAO, v["id"].removeprefix("li:"))
        if status in (429, 999):
            raise Bloqueio(f"LinkedIn limitou o acesso (HTTP {status}) no navegador")
        return self._extrair_descricao(texto) if status == 200 else ""

    # ------------------------------------------------------------------ comum
    def _ler_cards(self, html: str) -> list[dict]:
        cards = re.findall(r"<li[^>]*>(.*?)</li>", html, re.S)
        if not cards:
            if len(html) > 3000:
                self.avisos.append("página do LinkedIn sem cards reconhecidos (layout mudou?)")
            return []
        if not any(self._card(c) for c in cards):
            self.avisos.append("cards do LinkedIn nao puderam ser lidos (layout mudou?)")
        out, vistos = [], set()
        for c in cards:
            v = self._card(c)
            if v and v["id"] not in vistos:
                vistos.add(v["id"])
                out.append(v)
        return out

    def buscar(self, termo: str) -> list[dict]:
        if self.modo == "navegador":
            if self.parar and self.parar.is_set():
                return []
            vagas = self._buscar_navegador(termo)
            time.sleep(random.uniform(*self.pausa_busca))
            return vagas
        out, vistos = [], set()
        for pag in range(self.max_paginas):
            if self.parar and self.parar.is_set():
                break
            h = self._get(BUSCA, params={"keywords": termo, "geoId": GEO_BRASIL, "f_WT": "2",  # f_WT=2: remoto
                                         "f_TPR": f"r{self.dias * 86400}", "start": pag * 10})
            vagas = self._ler_cards(h)
            if not vagas:
                break
            for v in vagas:
                if v["id"] not in vistos:
                    vistos.add(v["id"])
                    out.append(v)
            time.sleep(random.uniform(*self.pausa_busca))
        return out

    @staticmethod
    def _card(c: str):
        m = re.search(r"jobPosting:(\d+)", c) or re.search(r"/jobs/view/[^\"?]*?(\d{6,})", c)
        if not m:
            return None

        def pega(pat):
            x = re.search(pat, c, re.S)
            return html_para_texto(x.group(1)) if x else ""

        titulo = pega(r'base-search-card__title[^>]*>(.*?)</h3>')
        if not titulo:  # link de vaga solto na pagina (menu, banner...): nao e um card de resultado
            return None
        link = re.search(r'href="([^"]*?/jobs/view/[^"?]+)', c)
        dt = re.search(r'datetime="(\d{4})-(\d{2})-(\d{2})"', c)
        idade = (date.today() - date(*map(int, dt.groups()))).days if dt else None
        return {
            "id": "li:" + m.group(1),
            "titulo": titulo,
            "empresa": pega(r'base-search-card__subtitle[^>]*>(.*?)</h4>'),
            "nota": None,
            "idade_dias": idade,
            "local": pega(r'job-search-card__location[^>]*>(.*?)</span>') or "Remoto",
            "easy": 0,
            "link": link.group(1) if link else f"https://www.linkedin.com/jobs/view/{m.group(1)}",
        }

    @staticmethod
    def _extrair_descricao(h: str) -> str:
        m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', h, re.S)
        return html_para_texto(m.group(1)) if m else ""

    def vale_descricao(self, v: dict) -> str:
        """Economiza requisições:
          'sim'    -> título indica RPA/automação: baixar a descrição;
          'nao'    -> título irrelevante/bloqueado: registrar sem descrição;
          'depois' -> título bom, mas o limite desta execução foi atingido: fica para a próxima."""
        r = pontuar(self.cfg, v["titulo"], "")
        if r is None or r <= 0:
            return "nao"
        return "sim" if self.baixadas < self.max_desc else "depois"

    def descricao(self, v: dict) -> str:
        self.baixadas += 1
        time.sleep(random.uniform(*self.pausa_desc))
        if self.modo == "navegador":
            return self._descricao_navegador(v)
        h = self._get(DETALHE.format(v["id"].removeprefix("li:")), referer=v.get("link") or "https://www.linkedin.com/jobs/search")
        return self._extrair_descricao(h)
