"""Verificador de vagas encerradas.

Seguro por padrao:
  - so consulta fontes publicas e estaveis (config "verificador.fontes", padrao gupy e programathor).
    Glassdoor e LinkedIn ficam de fora: o primeiro exige navegador e o segundo bloqueia com poucos acessos;
    a checagem simples tambem nao e confiavel neles;
  - pula fontes em pausa por bloqueio e para no primeiro 429 (colocando a fonte em pausa);
  - espera entre as requisicoes e verifica poucas vagas por vez (rodizio: as verificadas ha mais tempo primeiro);
  - so decide "encerrada" por status 404/410 ou por frase no <title>/<h1>/<h2>. Frases soltas no rodape,
    FAQ ou scripts da pagina NAO contam (era a causa de falsos positivos).
"""
import random
import re
import time

import requests

from . import banco, config
from .texto import html_para_texto, norm

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

# aplicado ao texto normalizado (sem acentos, minusculo) dos cabecalhos da pagina
PADROES_ENCERRADA = re.compile(
    r"vaga (?:encerrada|finalizada|expirada|indisponivel)|candidaturas? encerradas?|"
    r"nao esta mais disponivel|processo seletivo encerrado|esta vaga foi (?:finalizada|encerrada)|"
    r"job (?:has )?expired|no longer (?:available|accepting)|pagina nao encontrada|\b404\b"
)


class LimiteAtingido(Exception):
    """O site respondeu 429: parar de verificar nesta fonte."""


def _cabecalhos(html: str) -> str:
    partes = re.findall(r"<(?:title|h1|h2)[^>]*>(.*?)</(?:title|h1|h2)>", html or "", re.S | re.I)
    return norm(html_para_texto(" ".join(partes)))


def avaliar(sessao, link: str) -> str:
    """'encerrada', 'ativa' ou 'desconhecida' (erro, bloqueio ou resposta inesperada: NAO marca nada)."""
    try:
        r = sessao.get(link, timeout=10, allow_redirects=True)
    except Exception:
        return "desconhecida"
    if r.status_code == 429:
        raise LimiteAtingido()
    if r.status_code in (404, 410):
        return "encerrada"
    if r.status_code != 200:
        return "desconhecida"
    return "encerrada" if PADROES_ENCERRADA.search(_cabecalhos(r.text)) else "ativa"


def verificar_ativas(con, limite: int | None = None, pausa: tuple[float, float] = (1.5, 3.0)) -> dict:
    """Verifica vagas relevantes ainda abertas e marca encerrada=1 nas que fecharam."""
    cfg = config.carregar()
    vcfg = cfg.get("verificador", {})
    permitidas = vcfg.get("fontes", ["gupy", "programathor"])
    limite = limite or vcfg.get("limite", 30)

    puladas, usar = {}, []
    for f in permitidas:
        p = banco.pausa_ativa(con, f)
        if p:
            puladas[f] = f"em pausa ate {p[0][11:16]}"
        else:
            usar.append(f)
    if not usar:
        return {"verificadas": 0, "encerradas": 0, "puladas": puladas, "interrompida": False}

    marcas = ",".join("?" * len(usar))
    linhas = con.execute(
        f"SELECT id, link, fonte FROM vagas WHERE excluida=0 AND encerrada=0 AND link!='' AND score>=? "
        f"AND (duplicada_de IS NULL OR duplicada_de='') AND fonte IN ({marcas}) "
        f"ORDER BY COALESCE(verificada_em,'') ASC, visto_em ASC LIMIT ?",
        (cfg["pontuacao_minima"], *usar, limite)).fetchall()

    sessao = requests.Session()
    sessao.headers.update(HEADERS)
    verificadas = encerradas = 0
    interrompida = False
    try:
        for r in linhas:
            try:
                res = avaliar(sessao, r["link"])
            except LimiteAtingido:
                banco.pausar(con, r["fonte"], "verificador: HTTP 429", cfg["fontes"].get(r["fonte"], {}).get("pausa_base_horas", 3))
                puladas[r["fonte"]] = "limitou o acesso (429); fonte em pausa"
                interrompida = True
                break
            verificadas += 1
            if res == "encerrada":
                con.execute("UPDATE vagas SET encerrada=1, verificada_em=? WHERE id=?", (banco.agora(), r["id"]))
                encerradas += 1
            elif res == "ativa":
                con.execute("UPDATE vagas SET verificada_em=? WHERE id=?", (banco.agora(), r["id"]))
            time.sleep(random.uniform(*pausa))
    finally:
        sessao.close()
        con.commit()
    return {"verificadas": verificadas, "encerradas": encerradas, "puladas": puladas, "interrompida": interrompida}
