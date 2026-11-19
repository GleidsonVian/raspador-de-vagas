"""Programathor: busca pública de vagas remotas de tecnologia no Brasil.

Regras de segurança para evitar HTTP 429 (Cloudflare rate limit):
  - headers realistas de navegador desktop moderno (Chrome 131);
  - ritmo humanizado com pausas e jitter;
  - limite de descrições completas por execução (as demais ficam para as próximas);
  - retry suave antes de levantar Bloqueio.
"""
import random
import re
import time

import requests

from ..pontuacao import pontuar
from ..texto import html_para_texto

BASE_URL = "https://programathor.com.br"
BUSCA_URL = "https://programathor.com.br/jobs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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


class Programathor:
    NOME = "programathor"
    ROTULO = "Programathor"

    def __init__(self, cfg: dict, log, parar, **_):
        self.cfg, self.log, self.parar = cfg, log, parar
        fc = cfg.get("fontes", {}).get("programathor", {})
        self.max_paginas = fc.get("max_paginas", 1)
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

        headers = {"Referer": referer}
        try:
            r = self.s.get(url, params=params, headers=headers, timeout=25)
        except Exception:
            time.sleep(random.uniform(4, 8))
            r = self.s.get(url, params=params, headers=headers, timeout=25)

        if r.status_code == 429:
            espera = random.uniform(10, 18)
            self.log(f"[Programathor] Rate-limit (HTTP 429). Aguardando {espera:.1f}s antes de retentar...")
            time.sleep(espera)
            r = self.s.get(url, params=params, headers=headers, timeout=25)

        if r.status_code == 429:
            ra = r.headers.get("Retry-After", "")
            raise Bloqueio("Programathor limitou o acesso (HTTP 429)", int(ra) if ra.isdigit() else None)

        return r

    def buscar(self, termo: str) -> list[dict]:
        vagas: list[dict] = []
        vistos: set[str] = set()

        for pag in range(1, self.max_paginas + 1):
            if self.parar and self.parar.is_set():
                break

            r = self._get(BUSCA_URL, params={"q": termo, "page": pag})
            if not r.ok:
                self.avisos.append(f"Programathor retornou HTTP {r.status_code}")
                break

            cards = re.findall(r'<div class="cell-list\s*">(.*?)</a>\s*</div>', r.text, re.S)
            if not cards:
                break

            for c in cards:
                link_m = re.search(r'href="(/jobs/(\d+)-[^"]+)"', c)
                if not link_m:
                    continue
                path, jid = link_m.group(1), f"prog:{link_m.group(2)}"
                if jid in vistos:
                    continue
                vistos.add(jid)

                # Mantém apenas remotas
                texto_card = c.lower()
                if "remoto" not in texto_card and "home office" not in texto_card:
                    continue

                title_m = re.search(r'<h3[^>]*>(.*?)</h3>', c, re.S)
                titulo = html_para_texto(title_m.group(1)) if title_m else ""

                comp_m = re.search(r'alt="([^"]+)"', c)
                empresa = comp_m.group(1).strip() if comp_m else ""

                link = f"{BASE_URL}{path}"

                vagas.append({
                    "id": jid,
                    "titulo": titulo,
                    "empresa": empresa,
                    "nota": None,
                    "idade_dias": 0,
                    "local": "Remoto",
                    "easy": 0,
                    "link": link,
                    "tipo_raw": "PJ" if "pj" in texto_card else ("CLT" if "clt" in texto_card else ""), 
                })

            time.sleep(random.uniform(*self.pausa_busca))

        return vagas

    def vale_descricao(self, v: dict) -> str:
        """Evita baixar descrições de vagas irrelevantes e limita requisições por execução."""
        r = pontuar(self.cfg, v["titulo"], "")
        if r is None or r <= 0:
            return "nao"
        return "sim" if self.baixadas < self.max_desc else "depois"

    def descricao(self, v: dict) -> str:
        self.baixadas += 1
        time.sleep(random.uniform(*self.pausa_desc))
        try:
            r = self._get(v["link"], referer=BUSCA_URL)
            if not r.ok:
                return ""
            m = re.search(r'<div class="wrapper-content-job-show">(.*?)<div class="wrapper-content-company-show"', r.text, re.S)
            if not m:
                m = re.search(r'<div class="wrapper-content-job-show">(.*?)<footer', r.text, re.S)
            return html_para_texto(m.group(1)) if m else ""
        except Exception:
            return ""
