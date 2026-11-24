"""Simulador "e se eu aprendesse X?": recalcula o match das vagas relevantes com o seu perfil alterado.

E uma ESTIMATIVA com o calculo atual do match: aprender uma tecnologia passa a contar como habilidade sua
(peso `simulador.peso_habilidade`, padrao 3) e remove o desconto que ela tinha (lacunas, ferramentas fracas) e a
diluicao da cobertura. Nada e gravado: o perfil real so muda se voce editar o config.json.
"""
import copy

from . import analytics
from .pontuacao import explicar
from .tecnologias import agrupar
from .texto import norm

PESO_PADRAO = 3
CORTES_PADRAO = (40, 50, 60)


def _norm_todos(chaves) -> set[str]:
    return {norm(k) for k in chaves}


def termos_fora(cfg: dict) -> set[str]:
    """Tecnologias conhecidas que NAO estao no seu perfil (inclui as que ja descontam: lacunas e ferramentas fracas)."""
    perfil = _norm_todos(cfg["meu_perfil"])
    descontadas = _norm_todos(list(cfg.get("lacunas", {})) + list(cfg.get("ferramentas_fracas", [])))
    return (_norm_todos(cfg["tecnologias_outras"]) | descontadas) - perfil


def grupos_fora(cfg: dict) -> dict:
    return agrupar(sorted(termos_fora(cfg)))


def grupos_perfil(cfg: dict) -> dict:
    return agrupar(sorted(_norm_todos(cfg["meu_perfil"])))


def opcoes(cfg: dict, linhas: list[dict], limite: int = 40) -> list[dict]:
    """Tecnologias que faltam e aparecem nas vagas relevantes, da mais pedida para a menos pedida."""
    return analytics.mercado(linhas, cfg, lim_lacunas=limite, lim_fortes=0)["lacunas_mercado"]


def _aprendido(cfg: dict, termos: set[str], peso: int) -> dict:
    c = copy.deepcopy(cfg)
    for t in termos:
        c["meu_perfil"][t] = max(peso, c["meu_perfil"].get(t, 0))
    c["tecnologias_outras"] = [k for k in cfg["tecnologias_outras"] if norm(k) not in termos]
    c["lacunas"] = {k: v for k, v in cfg.get("lacunas", {}).items() if norm(k) not in termos}
    c["ferramentas_fracas"] = [k for k in cfg.get("ferramentas_fracas", []) if norm(k) not in termos]
    return c


def simular(cfg: dict, linhas: list[dict], tecnologias: list[str]) -> dict:
    """Levanta ValueError (mensagem para o usuario) se a tecnologia for desconhecida ou voce ja a tiver."""
    gf, gp = grupos_fora(cfg), grupos_perfil(cfg)
    pedidas = list(dict.fromkeys(tecnologias))
    desconhecidas = [t for t in pedidas if t not in gf and t not in gp]
    if desconhecidas:
        raise ValueError("tecnologia desconhecida: " + ", ".join(desconhecidas))
    ja_tem = [t for t in pedidas if t in gp]
    validas = [t for t in pedidas if t in gf]
    if not validas:
        raise ValueError("você já tem: " + ", ".join(ja_tem))

    sim = cfg.get("simulador", {})
    peso = sim.get("peso_habilidade", PESO_PADRAO)
    cortes = sim.get("cortes", list(CORTES_PADRAO))
    termos = set().union(*(gf[t] for t in validas))
    cfg2 = _aprendido(cfg, termos, peso)

    itens = []
    for r in linhas:
        desc = r.get("descricao") or ""
        antes = explicar(cfg, r["titulo"], desc)["match"]
        depois = explicar(cfg2, r["titulo"], desc)["match"]
        itens.append({"id": r["id"], "titulo": r["titulo"], "empresa": r["empresa"], "fonte": r["fonte"],
                      "antes": antes, "depois": depois, "delta": depois - antes})

    def posicoes(chave):
        ordem = sorted(itens, key=lambda i: (-i[chave], i["titulo"]))
        return {i["id"]: n + 1 for n, i in enumerate(ordem)}
    pos_antes, pos_depois = posicoes("antes"), posicoes("depois")
    for i in itens:
        i["pos_antes"], i["pos_depois"] = pos_antes[i["id"]], pos_depois[i["id"]]

    subiram = [i for i in itens if i["delta"] > 0]
    return {
        "tecnologias": validas, "ja_tem": ja_tem, "peso": peso, "total": len(itens), "subiram": len(subiram),
        "ganho_medio": round(sum(i["delta"] for i in subiram) / len(subiram), 1) if subiram else 0,
        "ganho_maximo": max((i["delta"] for i in subiram), default=0),
        "cortes": [{"corte": c, "antes": sum(1 for i in itens if i["antes"] >= c), "depois": sum(1 for i in itens if i["depois"] >= c)}
                   for c in cortes],
        "mudancas": sorted(subiram, key=lambda i: (-i["delta"], i["titulo"]))[:8],
        "melhores": sorted(itens, key=lambda i: (i["pos_depois"], i["titulo"]))[:8],
    }
