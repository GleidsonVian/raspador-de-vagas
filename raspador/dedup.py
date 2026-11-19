"""Deduplicação de vagas: identifica se a mesma vaga foi publicada em múltiplos sites ou re-publicada."""
import re

from .texto import norm


def chave_vaga(titulo: str, empresa: str) -> tuple[str, str]:
    """Chave única aproximada de empresa + título para identificar duplicatas."""
    emp = norm(empresa or "")
    emp = re.sub(r"\b(ltda|s\.?a\.?|inc|group|brasil|tecnologia|consultoria|solucoes|inform[aá]tica)\b", "", emp)
    emp = re.sub(r"[^a-z0-9]", "", emp)

    tit = norm(titulo or "")
    # Remove marcações de parênteses/colchetes e termos de localidade/contrato, preservando o nível (jr, pleno, sr)
    tit = re.sub(r"\(.*?\)|\[.*?\]", "", tit)
    tit = re.sub(r"\b(remoto|home\s*office|hibrido|clt|pj)\b", "", tit)
    tit = re.sub(r"[^a-z0-9]", "", tit)
    return emp, tit


def atualizar_duplicadas(con) -> int:
    """Varre todas as vagas ativas e marca 'duplicada_de' nas repetidas (mantendo a mais antiga como original)."""
    linhas = con.execute("SELECT id, titulo, empresa, visto_em, fonte FROM vagas ORDER BY visto_em ASC, id ASC").fetchall()
    vistos = {}
    duplicadas_count = 0

    for r in linhas:
        emp, tit = chave_vaga(r["titulo"], r["empresa"])
        if not emp or len(tit) < 4:
            continue
        chave = (emp, tit)
        if chave in vistos:
            original_id = vistos[chave]
            con.execute("UPDATE vagas SET duplicada_de=? WHERE id=?", (original_id, r["id"]))
            duplicadas_count += 1
        else:
            vistos[chave] = r["id"]
            con.execute("UPDATE vagas SET duplicada_de='' WHERE id=?", (r["id"],))

    con.commit()
    return duplicadas_count
