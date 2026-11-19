"""LinkedIn: SOMENTE a busca publica de vagas (sem login).

Regras de seguranca (o LinkedIn proibe automacao e suspende contas):
  - nunca usa a sua conta/cookies; so os endpoints publicos "jobs-guest";
  - volume baixo: poucos termos, 1 pagina, pausas longas, descricao so de titulos relevantes (maximo por execucao);
  - se responder 429/999 ou pedir login, a fonte e abandonada na hora e fica em PAUSA com backoff
    (3h, 6h, 12h, 24h) para o agendador nao insistir e piorar o bloqueio.
"""
import random
import re
import time
from datetime import date

import requests

from ..pontuacao import pontuar
from ..texto import html_para_texto

BUSCA = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETALHE = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
GEO_BRASIL = "106057199"
HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
    "accept-language": "pt-BR,pt;q=0.9,en;q=0.8",
}


class LinkedIn:
    NOME = "linkedin"
    ROTULO = "LinkedIn"

    def __init__(self, cfg: dict, log, parar, **_):
        self.cfg, self.log, self.parar = cfg, log, parar
        fc = cfg["fontes"]["linkedin"]
        self.max_paginas = fc.get("max_paginas", 2)
        self.dias = cfg.get("apenas_ultimos_dias", 30)
        self.pausa_busca = fc.get("pausa_busca_seg", [30, 60])
        self.pausa_desc = fc.get("pausa_descricao_seg", [18, 35])
        self.max_desc = fc.get("max_descricoes_por_execucao", 8)
        self.baixadas = 0
        self.avisos: list[str] = []  # sinais de layout mudado, lidos pelo runner

    def __enter__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        return self

    def __exit__(self, *exc):
        self.s.close()

    def _get(self, url, referer: str = "https://www.linkedin.com/jobs/search", **kw):
        from . import Bloqueio
        r = self.s.get(url, timeout=30, headers={"referer": referer}, **kw)
        if r.status_code in (429, 999) or "authwall" in r.url or "/login" in r.url:
            ra = r.headers.get("Retry-After", "")
            raise Bloqueio(f"LinkedIn limitou o acesso (HTTP {r.status_code})", int(ra) if ra.isdigit() else None)
        r.raise_for_status()
        return r.text

    def buscar(self, termo: str) -> list[dict]:
        out, vistos = [], set()
        for pag in range(self.max_paginas):
            if self.parar.is_set():
                break
            h = self._get(BUSCA, params={"keywords": termo, "geoId": GEO_BRASIL, "f_WT": "2",  # f_WT=2: remoto
                                         "f_TPR": f"r{self.dias * 86400}", "start": pag * 10})
            cards = re.findall(r"<li>(.*?)</li>", h, re.S)
            if not cards:
                if len(h) > 3000:  # pagina com conteudo mas sem cards reconhecidos
                    self.avisos.append("pagina do LinkedIn sem cards reconhecidos (layout mudou?)")
                break
            if not any(self._card(c) for c in cards):
                self.avisos.append("cards do LinkedIn nao puderam ser lidos (layout mudou?)")
            for c in cards:
                v = self._card(c)
                if v and v["id"] not in vistos:
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
        link = re.search(r'href="([^"]*?/jobs/view/[^"?]+)', c)
        dt = re.search(r'datetime="(\d{4})-(\d{2})-(\d{2})"', c)
        idade = (date.today() - date(*map(int, dt.groups()))).days if dt else None
        return {
            "id": "li:" + m.group(1),
            "titulo": pega(r'base-search-card__title[^>]*>(.*?)</h3>'),
            "empresa": pega(r'base-search-card__subtitle[^>]*>(.*?)</h4>'),
            "nota": None,
            "idade_dias": idade,
            "local": pega(r'job-search-card__location[^>]*>(.*?)</span>'),
            "easy": 0,
            "link": link.group(1) if link else f"https://www.linkedin.com/jobs/view/{m.group(1)}",
        }

    def vale_descricao(self, v: dict) -> str:
        """Economiza requisicoes (o LinkedIn corta depois de poucos acessos):
          "sim"    -> titulo indica RPA/automacao: baixar a descricao;
          "nao"    -> titulo irrelevante/bloqueado: registrar sem descricao (nao precisa revisitar);
          "depois" -> titulo bom, mas o limite de descricoes desta execucao acabou: NAO registrar,
                      assim a vaga e avaliada na proxima execucao."""
        r = pontuar(self.cfg, v["titulo"], "")
        if r is None or r <= 0:
            return "nao"
        return "sim" if self.baixadas < self.max_desc else "depois"

    def descricao(self, v: dict) -> str:
        self.baixadas += 1
        time.sleep(random.uniform(*self.pausa_desc))
        h = self._get(DETALHE.format(v["id"].removeprefix("li:")), referer=v.get("link") or "https://www.linkedin.com/jobs/search")
        m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', h, re.S)
        return html_para_texto(m.group(1)) if m else ""
