"""Glassdoor Brasil (remoto). Cloudflare bloqueia requests puros -> usa Edge real via CDP (perfil proprio).

Descoberto a partir do HAR:
  - busca: /Vaga/trabalho-remoto-brasil-<termo>-vagas-SRCH_IL.0,22_IS12226_KO23,<23+len(termo)>.htm
  - vagas embutidas no HTML (Next.js RSC) em "jobListings":[{jobview:{...}}]; paginas extras pelo botao "Ver mais vagas"
  - descricao: /job-listing/api/job-details?jobListingId=<id>&pageTypeEnum=SERP&queryString=/&countryId=36
"""
import json
import re
import time

from .. import navegador
from ..texto import html_para_texto, norm, slug

PREFIXO = "trabalho-remoto-brasil-"
LOC_REMOTO = 12226


def url_busca(termo: str) -> str:
    kw, ini = slug(termo), len(PREFIXO)
    return (f"https://www.glassdoor.com.br/Vaga/{PREFIXO}{kw}-vagas-"
            f"SRCH_IL.0,{ini - 1}_IS{LOC_REMOTO}_KO{ini},{ini + len(kw)}.htm")


def idade_texto(t: str):
    t = norm(t)
    if "mais de" in t:
        return 31
    m = re.search(r"(\d+)\s*dia", t)
    if m:
        return int(m.group(1))
    return 0 if re.search(r"\d+\s*h", t) else None


def extrair_jobs(page_html: str) -> list[dict]:
    partes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page_html, re.S)
    texto = "".join(json.loads('"' + p + '"') for p in partes)
    i = texto.find('"jobListings":[')
    if i < 0:
        return []
    arr, _ = json.JSONDecoder().raw_decode(texto, i + len('"jobListings":'))
    out = []
    for it in arr:
        jv = it.get("jobview", {})
        h, j = jv.get("header", {}), jv.get("job", {})
        emp = h.get("employer") or {}
        out.append({
            "id": str(j.get("listingId")),
            "titulo": h.get("jobTitleText") or j.get("jobTitleText") or "",
            "empresa": h.get("employerNameFromSearch") or emp.get("name") or "",
            "nota": (emp.get("ratings") or {}).get("overallRating"),
            "idade_dias": h.get("ageInDays"),
            "local": h.get("locationName") or "",
            "easy": int(bool(h.get("easyApply"))),
            "link": h.get("seoJobLink") or "",
        })
    return out


class Glassdoor:
    NOME = "glassdoor"
    ROTULO = "Glassdoor"

    @staticmethod
    def usa_navegador(cfg: dict) -> bool:
        return True  # divide o Edge/perfil com o LinkedIn: rodam um depois do outro

    def __init__(self, cfg: dict, log, parar, manter_aberto: bool = False):
        self.cfg, self.log, self.parar, self.manter = cfg, log, parar, manter_aberto
        self.max_paginas = cfg["fontes"]["glassdoor"].get("max_paginas", 3)
        self.avisos: list[str] = []  # sinais de layout mudado, lidos pelo runner

    # --- ciclo de vida -------------------------------------------------
    def __enter__(self):
        self.pw, self.br, self.ctx, self.page = navegador.abrir()
        return self

    def __exit__(self, *exc):
        navegador.fechar(self.pw, self.br, self.ctx, self.page, self.manter)

    # --- navegacao -----------------------------------------------------
    def _bloqueado(self) -> bool:
        try:
            t = (self.page.title() or "").lower()
        except Exception:  # navegando (ex.: desafio resolvido)
            time.sleep(1.5)
            return True
        return "security" in t or "just a moment" in t or "um momento" in t

    def _abrir(self, url: str):
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if self._bloqueado():
            self.log("Cloudflare pediu verificacao: clique em 'Confirme que e humano' na janela do Edge (espero ate 5 min)")
            for _ in range(150):
                if self.parar.is_set():
                    raise RuntimeError("cancelado")
                time.sleep(2)
                if not self._bloqueado():
                    break
            else:
                raise RuntimeError("Cloudflare nao liberou")
        try:
            self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

    def _mais_vagas(self, vistos: set) -> list[dict]:
        extra = []
        for _ in range(max(0, self.max_paginas - 1)):
            self.page.locator("li[data-jobid]").last.scroll_into_view_if_needed()
            time.sleep(1)
            bt = self.page.get_by_role("button", name="Ver mais vagas")
            if not bt.count():
                break
            self.page.keyboard.press("Escape")  # fecha modais que tampam o botao
            bt.first.evaluate("e => e.click()")
            time.sleep(3.5)
            for li in self.page.locator("li[data-jobid]").all():
                jid = li.get_attribute("data-jobid")
                if jid in vistos:
                    continue
                vistos.add(jid)

                def g(sel):
                    loc = li.locator(sel)
                    return loc.first.inner_text().strip() if loc.count() else ""
                a = li.locator('a[data-test="job-link"]')
                extra.append({"id": jid, "titulo": g('[data-test="job-title"]'),
                              "empresa": g('[class*="EmployerProfile_compactEmployerName"]'),
                              "nota": None, "idade_dias": idade_texto(g('[data-test="job-age"]')),
                              "local": g('[data-test="emp-location"]'), "easy": 0,
                              "link": a.first.get_attribute("href") if a.count() else ""})
        return extra

    # --- API da fonte ----------------------------------------------------
    def buscar(self, termo: str) -> list[dict]:
        self._abrir(url_busca(termo))
        jobs = extrair_jobs(self.page.content())
        if not jobs:
            n_dom = self.page.locator("li[data-jobid]").count()
            if n_dom:
                self.avisos.append(f"o parser leu 0 vagas, mas a pagina tem {n_dom} cards (layout do Glassdoor mudou?)")
        try:
            jobs += self._mais_vagas({j["id"] for j in jobs})
        except Exception as e:
            self.log(f"aviso: paginacao falhou, usando so a 1a pagina ({str(e)[:60]})")
        return jobs

    def descricao(self, v: dict) -> str:
        js = """async (id) => {
          const r = await fetch('/job-listing/api/job-details?jobListingId='+id+'&pageTypeEnum=SERP&queryString=%2F&countryId=36',
            {headers:{accept:'*/*'}});
          if(!r.ok) return '';
          const j = await r.json();
          return j.jobDescription || (j.jobOverview||{}).description || '';
        }"""
        try:
            return html_para_texto(self.page.evaluate(js, v["id"]) or "")
        except Exception:
            return ""
