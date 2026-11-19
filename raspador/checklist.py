"""Checklist por vaga: o que voce tem, o que falta e o que destacar na candidatura.

Sem IA: reaproveita a leitura por secoes do match (obrigatorio x diferencial; beneficios/sobre a empresa nao contam).
Os "destaques" (uma frase sua por habilidade, tirada do seu curriculo) ficam num arquivo LOCAL, fora do git:
perfil_local.json -> {"destaques": {"python": "...", "selenium": "..."}}. Veja perfil_local.example.json.
"""
import json

from . import config
from .pontuacao import segmentar
from .tecnologias import agrupar
from .texto import norm, tem

ARQ_LOCAL = config.BASE / "perfil_local.json"

NOMES_NIVEL = ["estágio", "júnior", "pleno", "sênior"]
_ALIAS_NIVEL = {"estagio": 0, "estagiario": 0, "junior": 1, "jr": 1, "pleno": 2, "pl": 2,
                "senior": 3, "sr": 3, "especialista": 3}


def carregar_destaques() -> dict[str, str]:
    """{habilidade normalizada: frase}. Arquivo ausente ou invalido = sem destaques (nunca levanta)."""
    try:
        d = json.loads(ARQ_LOCAL.read_text(encoding="utf-8")).get("destaques", {})
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {norm(str(k)).strip(): str(v).strip() for k, v in d.items() if str(v).strip()}


def _niveis_no_titulo(titulo: str) -> set[int]:
    t = norm(titulo)
    achados = {rank for palavra, rank in _ALIAS_NIVEL.items() if tem(palavra, t)}
    if "estagi" in t:
        achados.add(0)
    return achados


def avaliar_nivel(cfg: dict, titulo: str) -> dict:
    meu = _ALIAS_NIVEL.get(norm(cfg.get("meu_nivel", "pleno")), 2)
    pedidos = _niveis_no_titulo(titulo)
    if not pedidos:
        return {"avaliacao": "nao_informado", "texto": f"o título não informa o nível (o seu é {NOMES_NIVEL[meu]})"}
    nomes = " a ".join(dict.fromkeys(NOMES_NIVEL[r] for r in (min(pedidos), max(pedidos))))
    if meu in pedidos or min(pedidos) < meu < max(pedidos):
        return {"avaliacao": "compativel", "texto": f"{nomes}: compatível com o seu ({NOMES_NIVEL[meu]})"}
    if min(pedidos) > meu:
        return {"avaliacao": "acima", "texto": f"{nomes}: acima do seu nível ({NOMES_NIVEL[meu]})"}
    return {"avaliacao": "abaixo", "texto": f"{nomes}: abaixo do seu nível ({NOMES_NIVEL[meu]})"}


def montar(cfg: dict, v: dict, destaques: dict | None = None) -> dict:
    """v: linha da vaga (titulo, descricao, contrato, salario, easy, link, idade_dias, local, fonte...)."""
    destaques = carregar_destaques() if destaques is None else destaques
    desc = v.get("descricao") or ""
    info = {"contrato": v.get("contrato") or "", "salario": v.get("salario") or "", "candidatura_rapida": bool(v.get("easy")),
            "idade_dias": v.get("idade_dias"), "local": v.get("local") or "", "link": v.get("link") or "",
            "fonte": v.get("fonte") or ""}
    base = {"info": info, "nivel": avaliar_nivel(cfg, v.get("titulo") or ""), "tem_descricao": bool(desc.strip()),
            "voce_tem": [], "falta": [], "resumo": {"obrigatorias_total": 0, "cobertas": 0, "pct": 0}}
    if not base["tem_descricao"]:
        return base

    seg = segmentar(desc)
    pesos = {norm(k): w for k, w in cfg["meu_perfil"].items()}
    descontadas = {norm(k) for k in list(cfg.get("lacunas", {})) + list(cfg.get("ferramentas_fracas", []))}
    fora = ({norm(k) for k in cfg["tecnologias_outras"]} | descontadas) - set(pesos)

    def onde(termos):
        if seg["req"] and any(tem(t, seg["req"]) for t in termos):
            return "obrigatorio"
        if seg["dif"] and any(tem(t, seg["dif"]) for t in termos):
            return "diferencial"
        return None

    def ordem(item):
        return (0 if item["onde"] == "obrigatorio" else 1, -item.get("peso", 0), item["tech"])

    tem_, falta = [], []
    for rot, termos in agrupar(sorted(pesos)).items():
        o = onde(termos)
        if o:
            frase = next((destaques[t] for t in sorted(termos | {norm(rot)}) if t in destaques), "")
            tem_.append({"tech": rot, "onde": o, "peso": max(pesos[t] for t in termos), "destaque": frase})
    for rot, termos in agrupar(sorted(fora)).items():
        o = onde(termos)
        if o:
            falta.append({"tech": rot, "onde": o, "descontada": bool(termos & descontadas)})
    tem_.sort(key=ordem)
    falta.sort(key=ordem)

    cobertas = sum(1 for i in tem_ if i["onde"] == "obrigatorio")
    total = cobertas + sum(1 for i in falta if i["onde"] == "obrigatorio")
    base.update(voce_tem=[{k: v_ for k, v_ in i.items() if k != "peso"} for i in tem_], falta=falta,
                resumo={"obrigatorias_total": total, "cobertas": cobertas, "pct": round(100 * cobertas / total) if total else 0})
    return base
