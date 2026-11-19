"""LinkedIn: SOMENTE a busca pública de vagas (sem login).

Regras de segurança (o LinkedIn proíbe automação e suspende contas):
  - nunca usa a sua conta/cookies; só os endpoints públicos "jobs-guest";
  - volume baixo: poucos termos, 1 página, pausas humanas longas, descrição só de títulos relevantes (máximo por execução);
  - headers realistas de Chrome moderno para evitar fingerprint de bot;
  - se responder 429/999 ou pedir login, faz 1 tentativa com backoff e, se persistir, entra em PAUSA temporária.
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class LinkedIn:
    NOME = "linkedin"
    ROTULO = "LinkedIn"

    def __init__(self, cfg: dict, log, parar, **_):
        self.cfg, self.log, self.parar = cfg, log, parar
        fc = cfg.get("fontes", {}).get("linkedin", {})
        self.max_paginas = fc.get("max_paginas", 1)
        self.dias = cfg.get("apenas_ultimos_dias", 30)
        self.pausa_busca = fc.get("pausa_busca_seg", [25, 45])
        self.pausa_desc = fc.get("pausa_descricao_seg", [15, 28])
        self.max_desc = fc.get("max_descricoes_por_execucao", 6)
        self.baixadas = 0
        self.avisos: list[str] = []

    def __enter__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        return self

    def __exit__(self, *exc):
        self.s.close()

    def _get(self, url, referer: str = "https://www.linkedin.com/jobs/search", **kw):
        from . import Bloqueio

        # Primeira tentativa
        try:
            r = self.s.get(url, timeout=30, headers={"referer": referer}, **kw)
        except Exception as e:
            time.sleep(random.uniform(5, 10))
            r = self.s.get(url, timeout=30, headers={"referer": referer}, **kw)

        # Se vier 429 ou 999, tenta esperar um pouco e retentar uma vez com jitter
        if r.status_code in (429, 999):
            espera = random.uniform(12, 20)
            self.log(f"[LinkedIn] Rate-limit (HTTP {r.status_code}). Aguardando {espera:.1f}s antes de retentar...")
            time.sleep(espera)
            r = self.s.get(url, timeout=30, headers={"referer": referer}, **kw)

        if r.status_code in (429, 999) or "authwall" in r.url or "/login" in r.url:
            ra = r.headers.get("Retry-After", "")
            raise Bloqueio(f"LinkedIn limitou o acesso (HTTP {r.status_code})", int(ra) if ra.isdigit() else None)

        r.raise_for_status()
        return r.text

    def buscar(self, termo: str) -> list[dict]:
        out, vistos = [], set()
        for pag in range(self.max_paginas):
            if self.parar and self.parar.is_set():
                break

            h = self._get(BUSCA, params={
                "keywords": termo,
                "geoId": GEO_BRASIL,
                "f_WT": "2",  # f_WT=2: remoto
                "f_TPR": f"r{self.dias * 86400}",
                "start": pag * 10
            })

            cards = re.findall(r"<li>(.*?)</li>", h, re.S)
            if not cards:
                if len(h) > 3000:
                    self.avisos.append("página do LinkedIn sem cards reconhecidos (layout mudou?)")
                break

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
            "local": pega(r'job-search-card__location[^>]*>(.*?)</span>') or "Remoto",
            "easy": 0,
            "link": link.group(1) if link else f"https://www.linkedin.com/jobs/view/{m.group(1)}",
        }

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
        h = self._get(DETALHE.format(v["id"].removeprefix("li:")), referer=v.get("link") or "https://www.linkedin.com/jobs/search")
        m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', h, re.S)
        return html_para_texto(m.group(1)) if m else ""
