"""Módulo de estatísticas, métricas e tendências de mercado para o painel."""
import re
from collections import Counter
from typing import Any, Dict, List

from . import config
from .tecnologias import agrupar


TECH_MAP = [
    ("Python", r"\bpython\b"),
    ("SQL", r"\bsql\b"),
    ("UiPath", r"\buipath\b"),
    ("Power Automate", r"\bpower\s*automate\b"),
    ("Selenium", r"\bselenium\b"),
    ("n8n", r"\bn8n\b"),
    ("REST / APIs", r"\b(?:apis?|rest(?:ful)?)\b"),
    ("Git", r"\bgit\b"),
    ("AWS", r"\baws\b"),
    ("Azure", r"\bazure\b"),
    ("Docker", r"\bdocker\b"),
    ("TypeScript / JS", r"\b(?:typescript|javascript|node(?:\.js)?)\b"),
    ("Playwright", r"\bplaywright\b"),
    ("C# / .NET", r"\b(?:c#|\.net)\b"),
    ("IA / GenAI", r"\b(?:ia|intelig[eê]ncia artificial|llm|langchain|genai)\b"),
    ("Automation Anywhere", r"\bautomation\s*anywhere\b"),
    ("Pandas / Dados", r"\b(?:pandas|etl|power\s*bi)\b"),
    ("Linux", r"\blinux\b"),
]


def _mercado(linhas, cfg: dict) -> dict:
    """O que as vagas relevantes pedem: (a) tecnologias que voce AINDA NAO tem e (b) seus pontos fortes mais pedidos.
    Conta por vaga, separando 'obrigatorio' (requisitos/responsabilidades) de 'diferencial'; o que esta em
    beneficios/sobre a empresa nao conta (mesma regra do match)."""
    from .pontuacao import segmentar
    from .texto import norm, tem

    perfil = {norm(k) for k in cfg["meu_perfil"]}
    descontadas = {norm(k) for k in list(cfg.get("lacunas", {})) + list(cfg.get("ferramentas_fracas", []))}
    fora = ({norm(k) for k in cfg["tecnologias_outras"]} | descontadas) - perfil
    grupos_fora, grupos_perfil = agrupar(sorted(fora)), agrupar(sorted(perfil))
    contagem = {rot: [0, 0] for rot in list(grupos_fora) + list(grupos_perfil)}
    todos = {**grupos_fora, **grupos_perfil}

    for r in linhas:
        seg = segmentar(r["descricao"] or "")
        for rot, termos in todos.items():
            if seg["req"] and any(tem(t, seg["req"]) for t in termos):
                contagem[rot][0] += 1
            elif seg["dif"] and any(tem(t, seg["dif"]) for t in termos):
                contagem[rot][1] += 1

    total = len(linhas)

    def montar(grupos, limite):
        itens = [{"tech": rot, "vagas": o + d, "obrigatorio": o, "diferencial": d,
                  "pct": round(100 * (o + d) / total, 1), "penalizada": bool(termos & descontadas)}
                 for rot, termos in grupos.items() for o, d in [contagem[rot]] if o + d]
        itens.sort(key=lambda i: (-i["vagas"], -i["obrigatorio"], i["tech"]))
        return itens[:limite]

    return {"lacunas_mercado": montar(grupos_fora, 10), "pontos_fortes": montar(grupos_perfil, 8)}


def _parse_salario_medio(s: str) -> float | None:
    if not s:
        return None
    # Extrai números: 5.000, 7.500, etc.
    nums_str = re.findall(r"(\d+(?:[.,]\d+)?)", s.replace("R$", ""))
    valid_nums = []
    for raw in nums_str:
        try:
            val = float(raw.replace(".", "").replace(",", "."))
            # Ajuste de escala (ex: 7.5 = 7500)
            if val < 80:
                val = val * 1000
            if 800 <= val <= 60000:  # Faixa salarial plausível
                valid_nums.append(val)
        except (ValueError, TypeError):
            continue
    if not valid_nums:
        return None
    return sum(valid_nums) / len(valid_nums)


def obter_analytics(con, minimo: int = 10, cfg: dict | None = None) -> Dict[str, Any]:
    """Estatisticas de mercado sobre as vagas RELEVANTES (score >= minimo), abertas e nao duplicadas.
    Antes entravam todas as lidas, e ~96% eram irrelevantes: os numeros nao significavam nada."""
    linhas = con.execute(
        """
        SELECT id, titulo, empresa, salario, contrato, descricao, fonte, match
        FROM vagas
        WHERE excluida = 0 AND (encerrada IS NULL OR encerrada = 0)
          AND (duplicada_de IS NULL OR duplicada_de = '') AND score >= ?
        """,
        (minimo,),
    ).fetchall()

    total = len(linhas)
    if total == 0:
        return {
            "total_vagas": 0,
            "top_tecnologias": [],
            "contratos": [],
            "senioridades": [],
            "top_empresas": [],
            "salarios": {
                "com_salario": 0,
                "pct_com_salario": 0,
                "media_geral": 0,
                "media_clt": 0,
                "media_pj": 0,
                "min": 0,
                "max": 0,
            },
            "fontes": [],
            "lacunas_mercado": [],
            "pontos_fortes": [],
        }

    tech_counter: Counter[str] = Counter()
    contratos_counter: Counter[str] = Counter()
    senioridades_counter: Counter[str] = Counter()
    empresas_counter: Counter[str] = Counter()
    fontes_counter: Counter[str] = Counter()
    fontes_matches: Dict[str, List[int]] = {}

    salarios_geral: List[float] = []
    salarios_clt: List[float] = []
    salarios_pj: List[float] = []

    for r in linhas:
        tit = (r["titulo"] or "").lower()
        desc = (r["descricao"] or "").lower()
        texto_completo = f"{tit} {desc}"

        # 1. Contratos
        c = (r["contrato"] or "Não informado").strip()
        contratos_counter[c] += 1

        # 2. Empresas (ignora genéricas / confidencial)
        emp = (r["empresa"] or "").strip()
        emp_clean = re.sub(r"\s+", " ", emp)
        if emp_clean and emp_clean.lower() not in (
            "empresa confidencial",
            "confidencial",
            "não informada",
            "vaga confidencial",
            "indisponível",
        ):
            empresas_counter[emp_clean] += 1

        # 3. Fontes & Matches
        f = r["fonte"]
        fontes_counter[f] += 1
        m = r["match"] or 0
        if f not in fontes_matches:
            fontes_matches[f] = []
        fontes_matches[f].append(m)

        # 4. Senioridade
        if re.search(r"\b(jr|junior|júnior|estagi[aá]ri[oa]|trainee)\b", tit):
            senioridades_counter["Júnior / Estágio"] += 1
        elif re.search(r"\b(sr|senior|sênior|especialista|tech\s*lead|líder|staff|principal)\b", tit):
            senioridades_counter["Sênior / Especialista"] += 1
        elif re.search(r"\b(pl|pleno)\b", tit):
            senioridades_counter["Pleno"] += 1
        else:
            senioridades_counter["Não especificado"] += 1

        # 5. Salários
        sal_val = _parse_salario_medio(r["salario"])
        if sal_val:
            salarios_geral.append(sal_val)
            contrato_str = (r["contrato"] or "").upper()
            if "PJ" in contrato_str:
                salarios_pj.append(sal_val)
            if "CLT" in contrato_str:
                salarios_clt.append(sal_val)

        # 6. Tecnologias
        for nome_tech, regex_tech in TECH_MAP:
            if re.search(regex_tech, texto_completo):
                tech_counter[nome_tech] += 1

    # Formatar Top Tecnologias
    top_techs = [
        {"tech": k, "count": v, "pct": round((v / total) * 100, 1)}
        for k, v in tech_counter.most_common(12)
    ]

    # Formatar Contratos
    lista_contratos = [
        {"tipo": k, "count": v, "pct": round((v / total) * 100, 1)}
        for k, v in contratos_counter.most_common()
    ]

    # Formatar Senioridades
    ordem_sen = ["Júnior / Estágio", "Pleno", "Sênior / Especialista", "Não especificado"]
    lista_senioridades = [
        {"nivel": n, "count": senioridades_counter[n], "pct": round((senioridades_counter[n] / total) * 100, 1)}
        for n in ordem_sen
        if senioridades_counter[n] > 0
    ]

    # Formatar Top Empresas
    top_empresas = [
        {"empresa": k, "count": v}
        for k, v in empresas_counter.most_common(10)
    ]

    # Formatar Fontes
    lista_fontes = []
    for f_nome, f_count in fontes_counter.most_common():
        matches = fontes_matches.get(f_nome, [])
        media_m = round(sum(matches) / len(matches), 1) if matches else 0
        relevantes = sum(1 for m in matches if m >= 60)  # vagas com match forte
        lista_fontes.append({
            "fonte": f_nome,
            "total": f_count,
            "media_match": media_m,
            "relevantes": relevantes,
        })

    # Estatísticas Salariais
    stats_salarios = {
        "com_salario": len(salarios_geral),
        "pct_com_salario": round((len(salarios_geral) / total) * 100, 1),
        "media_geral": round(sum(salarios_geral) / len(salarios_geral), 2) if salarios_geral else 0,
        "media_clt": round(sum(salarios_clt) / len(salarios_clt), 2) if salarios_clt else 0,
        "media_pj": round(sum(salarios_pj) / len(salarios_pj), 2) if salarios_pj else 0,
        "min": round(min(salarios_geral), 2) if salarios_geral else 0,
        "max": round(max(salarios_geral), 2) if salarios_geral else 0,
    }

    return {
        "total_vagas": total,
        "top_tecnologias": top_techs,
        "contratos": lista_contratos,
        "senioridades": lista_senioridades,
        "top_empresas": top_empresas,
        "salarios": stats_salarios,
        "fontes": lista_fontes,
        **_mercado(linhas, cfg or config.carregar()),
    }
