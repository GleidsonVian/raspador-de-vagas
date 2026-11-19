"""Deduplicacao de vagas: identifica a mesma vaga publicada em varios sites ou republicada.

A chave e (empresa, titulo) normalizados. O texto entre parenteses FICA na chave, porque costuma ser o nivel
("RPA Developer (Junior)" x "(Senior)" sao vagas diferentes). Preferimos errar para o lado de NAO esconder vagas.
"""
import re

from .texto import norm

_RUIDO_EMPRESA = re.compile(r"\b(ltda|s\.?a\.?|inc|group|brasil|tecnologia|consultoria|solucoes|informatica)\b")
# marcas que nao identificam a vaga: modelo/contrato e selo de "nova"
_RUIDO_TITULO = re.compile(r"\b(remoto|home\s*office|hibrido|clt|pj|nova|novo)\b")


def chave_vaga(titulo: str, empresa: str) -> tuple[str, str]:
    emp = _RUIDO_EMPRESA.sub("", norm(empresa or ""))
    emp = re.sub(r"[^a-z0-9]", "", emp)
    tit = re.sub(r"\[.*?\]", "", norm(titulo or ""))  # [Remoto], [Tecnologia]: etiquetas, nao o cargo
    tit = _RUIDO_TITULO.sub("", tit)
    tit = re.sub(r"[^a-z0-9]", "", tit)
    return emp, tit


def _valida(chave: tuple[str, str]) -> bool:
    return bool(chave[0]) and len(chave[1]) >= 4


def carregar_chaves(con) -> dict:
    """{(empresa, titulo): id} das vagas que ja estao no banco (a mais antiga de cada chave). Usado para
    nao baixar a descricao de uma vaga que ja existe em outra fonte."""
    chaves = {}
    for r in con.execute("SELECT id, titulo, empresa FROM vagas ORDER BY visto_em ASC, id ASC"):
        k = chave_vaga(r["titulo"], r["empresa"])
        if _valida(k):
            chaves.setdefault(k, r["id"])
    return chaves


def atualizar_duplicadas(con) -> int:
    """Marca 'duplicada_de' nas repetidas (a mais antiga de cada grupo e o original)."""
    linhas = con.execute("SELECT id, titulo, empresa FROM vagas ORDER BY visto_em ASC, id ASC").fetchall()
    vistos, n = {}, 0
    for r in linhas:
        k = chave_vaga(r["titulo"], r["empresa"])
        if not _valida(k):
            continue
        if k in vistos:
            con.execute("UPDATE vagas SET duplicada_de=? WHERE id=?", (vistos[k], r["id"]))
            n += 1
        else:
            vistos[k] = r["id"]
            con.execute("UPDATE vagas SET duplicada_de='' WHERE id=?", (r["id"],))
    con.commit()
    return n
