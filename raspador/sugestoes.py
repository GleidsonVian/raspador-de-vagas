"""Sugestoes de lacunas a partir das vagas que voce marcou como "Descartado".

Se uma tecnologia aparece muito nas vagas que voce descarta e pouco nas que voce curte (Interessado/Candidatado),
o painel sugere adicioná-la as lacunas (desconto no match). Quem decide e voce: aplicar grava no config.json
e ignorar guarda a recusa em `lacunas_ignoradas` para a sugestao nao voltar.
"""
import copy

from .pontuacao import segmentar
from .simulador import grupos_fora
from .texto import norm, tem

PADRAO = {"min_descartadas": 3, "min_ocorrencias": 2, "pct_minimo": 30, "penalidade": 8}
POSITIVAS = ("interessado", "candidatado")


def _texto_util(desc: str) -> str:
    """Requisitos + diferenciais (beneficios e 'sobre a empresa' nao contam, como no match)."""
    seg = segmentar(desc or "")
    return seg["req"] + "\n" + seg["dif"]


def _contar(termos: set[str], textos: list[str]) -> int:
    return sum(1 for t in textos if any(tem(x, t) for x in termos))


def sugerir(cfg: dict, linhas: list[dict]) -> dict:
    """linhas: [{status, descricao}, ...] das vagas relevantes."""
    c = {**PADRAO, **cfg.get("sugestoes", {})}
    desc = [_texto_util(r["descricao"]) for r in linhas if r["status"] == "descartado"]
    pos = [_texto_util(r["descricao"]) for r in linhas if r["status"] in POSITIVAS]
    base = {"descartadas": len(desc), "positivas": len(pos), "minimo": c["min_descartadas"],
            "suficiente": len(desc) >= c["min_descartadas"], "sugestoes": [], "revisar": []}
    if not base["suficiente"]:
        return base

    ignoradas = {norm(x) for x in cfg.get("lacunas_ignoradas", [])}
    ja_descontadas = {norm(k) for k in list(cfg.get("lacunas", {})) + list(cfg.get("ferramentas_fracas", []))}
    out = []
    for rotulo, termos in grupos_fora(cfg).items():
        if termos & ja_descontadas or termos & ignoradas or norm(rotulo) in ignoradas:
            continue
        nd = _contar(termos, desc)
        pct_d = 100 * nd / len(desc)
        if nd < c["min_ocorrencias"] or pct_d < c["pct_minimo"]:
            continue
        npos = _contar(termos, pos)
        if pos and 100 * npos / len(pos) > pct_d / 2:   # tambem aparece muito no que voce curte: nao e motivo de descarte
            continue
        out.append({"tech": rotulo, "descartadas": nd, "pct_descartadas": round(pct_d), "positivas": npos,
                    "pen_sugerida": c["penalidade"]})
    out.sort(key=lambda i: (-i["descartadas"], i["tech"]))
    base["sugestoes"] = out[:10]

    # o contrario: tecnologias que JA descontam, mas aparecem em boa parte das vagas que voce curtiu/candidatou
    # (o desconto pode estar escondendo vagas que voce quer). So informativo: nao altera nada.
    rev = []
    if len(pos) >= c.get("min_positivas", 2):
        for rotulo, termos in grupos_fora(cfg).items():
            if not termos & ja_descontadas:
                continue
            npos = _contar(termos, pos)
            pct_p = 100 * npos / len(pos)
            nd = _contar(termos, desc)
            pct_d = 100 * nd / len(desc)
            if npos >= 2 and pct_p >= 50 and pct_p >= pct_d:
                rev.append({"tech": rotulo, "positivas": npos, "pct_positivas": round(pct_p),
                            "descartadas": nd, "pct_descartadas": round(pct_d)})
    rev.sort(key=lambda i: (-i["pct_positivas"], i["tech"]))
    base["revisar"] = rev[:5]
    return base


def aplicar(cfg: dict, tecnologia: str, pen: int, linhas: list[dict]) -> tuple[dict, str, int]:
    """Devolve (novo_cfg, termo_gravado, penalidade). Grava UM termo por tecnologia (o mais citado nas descartadas),
    para nao somar o desconto de varias grafias (GCP e Google Cloud)."""
    grupos = grupos_fora(cfg)
    if tecnologia not in grupos:
        raise ValueError("tecnologia desconhecida ou que você já tem: " + tecnologia)
    termos = grupos[tecnologia]
    descartadas = [_texto_util(r["descricao"]) for r in linhas if r["status"] == "descartado"]
    termo = max(sorted(termos), key=lambda t: _contar({t}, descartadas))
    pen = max(1, min(int(pen), 30))
    novo = copy.deepcopy(cfg)
    novo.setdefault("lacunas", {})[termo] = pen
    alvo = {norm(tecnologia)} | termos
    novo["lacunas_ignoradas"] = [x for x in novo.get("lacunas_ignoradas", []) if norm(x) not in alvo]
    return novo, termo, pen


def ignorar(cfg: dict, tecnologia: str) -> dict:
    if tecnologia not in grupos_fora(cfg):
        raise ValueError("tecnologia desconhecida: " + tecnologia)
    novo = copy.deepcopy(cfg)
    ign = novo.setdefault("lacunas_ignoradas", [])
    if tecnologia not in ign:
        ign.append(tecnologia)
    return novo
