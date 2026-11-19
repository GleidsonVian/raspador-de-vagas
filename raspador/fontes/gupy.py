"""Gupy: busca publica de vagas remotas via API REST do Portal Gupy.

Endpoints descobertos do Portal Gupy:
  - busca: https://portal.gupy.io/api/job-search/jobs?jobName={termo}&workplaceType=remote&limit={limite}&offset={offset}
  - traz lista paginada e ja inclui a descricao completa de cada vaga no payload JSON.
"""
import random
import time
from datetime import datetime, timezone

import requests

from ..pontuacao import pontuar
from ..texto import html_para_texto

URL_BUSCA = "https://portal.gupy.io/api/job-search/jobs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://portal.gupy.io/job-search",
}


def _idade_dias(dt_str: str | None) -> int | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return None


class Gupy:
    NOME = "gupy"
    ROTULO = "Gupy"

    def __init__(self, cfg: dict, log, parar, **_):
        self.cfg, self.log, self.parar = cfg, log, parar
        fc = cfg.get("fontes", {}).get("gupy", {})
        self.max_paginas = fc.get("max_paginas", 3)
        self.limite_por_pagina = fc.get("limite_por_pagina", 20)
        self.pausa_busca = fc.get("pausa_busca_seg", [1.0, 2.5])
        self._descricoes: dict[str, str] = {}
        self.avisos: list[str] = []

    def __enter__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        return self

    def __exit__(self, *exc):
        self.s.close()

    def buscar(self, termo: str) -> list[dict]:
        from . import Bloqueio

        vagas: list[dict] = []
        vistos: set[str] = set()

        for pag in range(self.max_paginas):
            if self.parar and self.parar.is_set():
                break

            params = {
                "jobName": termo,
                "workplaceType": "remote",
                "limit": self.limite_por_pagina,
                "offset": pag * self.limite_por_pagina,
            }

            try:
                r = self.s.get(URL_BUSCA, params=params, timeout=25)
            except Exception as e:
                self.avisos.append(f"Gupy falhou na pagina {pag + 1}: {str(e)[:80]}")
                break

            if r.status_code == 429:
                ra = r.headers.get("Retry-After", "")
                raise Bloqueio("Gupy limitou o acesso (HTTP 429)", int(ra) if ra.isdigit() else None)

            if not r.ok:
                self.avisos.append(f"Gupy retornou HTTP {r.status_code}")
                break

            try:
                dados = r.json()
            except Exception:
                self.avisos.append("Gupy nao retornou JSON valido")
                break

            itens = dados.get("data") or []
            if not itens:
                break

            for it in itens:
                jid = f"gupy:{it.get('id')}"
                if jid in vistos:
                    continue
                vistos.add(jid)

                titulo = (it.get("name") or "").strip()
                empresa = (it.get("careerPageName") or "").strip()
                cidade = (it.get("city") or "").strip()
                estado = (it.get("state") or "").strip()
                loc_uf = f"{cidade} - {estado}".strip(" -")
                local = f"Remoto ({loc_uf})" if loc_uf else "Remoto"

                link = (it.get("jobUrl") or it.get("careerPageUrl") or "").strip()
                if link and not link.startswith("http"):
                    link = f"https://portal.gupy.io{link}"

                raw_desc = it.get("description") or ""
                desc = html_para_texto(raw_desc)
                self._descricoes[jid] = desc

                badges = it.get("badges") or {}
                easy = 1 if badges.get("isQuickApply") else 0

                vagas.append({
                    "id": jid,
                    "titulo": titulo,
                    "empresa": empresa,
                    "nota": None,
                    "idade_dias": _idade_dias(it.get("publishedDate")),
                    "local": local,
                    "easy": easy,
                    "link": link,
                    "descricao": desc,
                    "tipo_raw": it.get("type", ""), 
                })

            total = (dados.get("pagination") or {}).get("total")
            if total is not None and (pag + 1) * self.limite_por_pagina >= total:
                break

            time.sleep(random.uniform(*self.pausa_busca))

        return vagas

    def vale_descricao(self, v: dict) -> str:
        """Evita processar vagas com título bloqueado."""
        r = pontuar(self.cfg, v["titulo"], "")
        if r is None:
            return "nao"
        return "sim"

    def descricao(self, v: dict) -> str:
        return self._descricoes.get(v["id"]) or v.get("descricao") or ""
