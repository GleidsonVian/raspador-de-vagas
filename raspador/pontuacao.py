"""Relevancia (filtro) e % de aderencia ao perfil. Tudo parametrizado por config.json.

A descricao e dividida em secoes: o que esta em "Requisitos/Responsabilidades" pesa 100%,
o que esta em "Diferenciais/Desejavel" pesa pouco (peso_diferencial) e "Beneficios/Sobre a empresa" nao conta.
explicar() devolve o detalhe de como o match foi calculado (mostrado no painel).
"""
import re

from .texto import norm, tem

# marcadores de secao (ja sem acentos e minusculos, pois o texto passa por norm())
_M = {
    "dif": r"diferenciais?|desejaveis|desejavel|nice to have|pontos extras|conhecimentos? adicionais|sera um diferencial",
    "ign": r"beneficios|o que oferecemos|sobre (?:a|o) (?:empresa|nos)|quem somos|nossa cultura|"
           r"informacoes adicionais|processo seletivo|diversidade e inclusao",
    "req": r"requisitos?(?: obrigatorios?)?|obrigatorios?|qualificacoes|o que esperamos|o que voce precisa|"
           r"responsabilidades|atividades|principais atividades|must have|requirements|responsibilities|what you",
}
_ALT = "|".join(f"(?P<{k}>{v})" for k, v in _M.items())
# cabecalho no inicio da linha ("Diferenciais:" ou so "Diferenciais" numa linha curta)
_HEAD_LINHA = re.compile(rf"(?:^|\n)[ \t\-•*]*(?:{_ALT})[^\n:]{{0,40}}?(?::|(?=\n)|$)")
# cabecalho no meio de um texto corrido (descricoes antigas sem quebra de linha): exige ":" apos ponto final
_HEAD_INLINE = re.compile(rf"(?<=[.;!?])\s+(?:{_ALT})\s*:")
# linha isolada que se declara opcional
_LINHA_DIF = re.compile(r"diferencial|desejavel|nice to have|\bplus\b|sera valorizad|valorizamos|e um bonus")


def _segmentos(desc: str) -> dict[str, str]:
    """Agrupa o texto normalizado da descricao em {'req': ..., 'dif': ..., 'ign': ...}."""
    txt = norm(desc)
    marcas = []
    for rx in (_HEAD_LINHA, _HEAD_INLINE):
        for m in rx.finditer(txt):
            modo = next(k for k in ("dif", "ign", "req") if m.group(k))
            marcas.append((m.start(), m.end(), modo))
    marcas.sort()
    pedacos, pos, modo = [], 0, "req"
    for ini, fim, novo in marcas:
        if ini < pos:
            continue
        pedacos.append((modo, txt[pos:ini]))
        modo, pos = novo, fim
    pedacos.append((modo, txt[pos:]))
    out = {"req": [], "dif": [], "ign": []}
    for modo, trecho in pedacos:
        if modo == "req":  # linha a linha: algumas se declaram "diferencial"/"desejavel"
            for linha in trecho.split("\n"):
                out["dif" if _LINHA_DIF.search(linha) else "req"].append(linha)
        else:
            out[modo].append(trecho)
    return {k: "\n".join(v) for k, v in out.items()}


def segmentar(desc: str) -> dict[str, str]:
    """{'req': ..., 'dif': ..., 'ign': ...} com o texto normalizado de cada secao (usado pelas estatisticas)."""
    return _segmentos(desc)


def pontuar(cfg: dict, titulo: str, desc: str) -> int | None:
    """None = bloqueada; 0 = sem sinal de RPA; >0 = relevante (compare com pontuacao_minima)."""
    t = norm(titulo)
    if any(b in t for b in cfg["bloqueia_titulo"]):
        return None
    # QA/teste nao e o foco, a menos que o titulo cite RPA/UiPath/n8n/Selenium/Python
    if any(tem(b, t) for b in cfg["bloqueia_qa"]) and not any(tem(e, t) for e in cfg["bloqueia_qa_exceto"]):
        return None
    d = norm(desc)
    nuc = [norm(k) for k in cfg["termos_nucleo"]]
    tit_ok = any(tem(k, t) for k in nuc) or any(k in t for k in cfg["termos_titulo_extra"])
    if not (tit_ok or sum(tem(k, d) for k in nuc) >= 2):
        return 0
    s = sum(p for k, p in cfg["pontos_titulo"].items() if k in t)
    s += sum(p for k, p in cfg["pontos_descricao"].items() if norm(k) in d)
    return s


def explicar(cfg: dict, titulo: str, desc: str) -> dict:
    """Calcula o % de aderencia e devolve o detalhe (habilidades, lacunas, ajustes) para exibir no painel."""
    t = norm(titulo)
    seg = _segmentos(desc)
    pesos = {"req": 1.0, "dif": cfg.get("peso_diferencial", 0.3), "ign": 0.0}

    def peso(k: str) -> float:
        return max((pesos[m] for m, texto in seg.items() if texto and tem(k, texto)), default=0.0)

    skills, a = [], 0.0
    for k, w in cfg["meu_perfil"].items():
        p = peso(k)
        if p > 0:
            skills.append({"t": k, "dif": p < 1})
            a += w * p
    outras, b = [], 0.0
    for k in cfg["tecnologias_outras"]:
        if k in cfg["meu_perfil"]:
            continue
        p = peso(k)
        if p > 0:
            outras.append(k)
            b += 2 * p
    # suavizacao: com pouca evidencia (poucas tecnologias citadas) a cobertura nao pode ir a 100%.
    # "prior" = pontos de evidencia que a vaga precisa ter para a cobertura ser confiavel.
    prior = cfg.get("cobertura_prior", 0)
    cobertura = a / (a + b + prior) if (a + b + prior) else 0.0

    foco = ("rpa", "uipath", "n8n", "selenium", "power automate", "automation anywhere", "blue prism")
    # "automacao" generica no titulo so conta como foco se a descricao (requisitos/diferenciais) mostrar sinal de
    # RPA/automacao tecnica; senao e tratada como titulo comum (ex.: "Consultor Zendesk CX e Automacao")
    sinal = any(tem(k, seg[m]) for m in ("req", "dif") if seg[m] for k in cfg["termos_nucleo"])
    if any(tem(k, t) for k in foco) or ("automa" in t and sinal):
        tit, tit_txt = 1.0, "foco"
    elif any(tem(k, t) for k in ("python", "selenium", "scraping", "integracao", "integracoes")):
        tit, tit_txt = 0.5, "parcial"
    else:
        tit, tit_txt = 0.1, "fraco"

    nivel = {k: v for k, v in cfg["nivel"].items() if tem(k, t)}
    ajuste = float(sum(nivel.values()))

    pen, fraca = cfg["penalidade_fracas"], None
    em_titulo = [k for k in cfg["ferramentas_fracas"] if tem(k, t)]
    if em_titulo:
        ajuste -= pen["titulo"]
        fraca = {"t": em_titulo, "onde": "titulo", "pen": pen["titulo"]}
    else:
        achadas = {k: peso(k) for k in cfg["ferramentas_fracas"] if peso(k) > 0}
        if achadas:
            p = round(pen["descricao"] * max(achadas.values()), 1)
            ajuste -= p
            fraca = {"t": list(achadas), "onde": "descricao", "pen": p}

    lacunas, total = [], 0.0
    for k, v in cfg.get("lacunas", {}).items():
        p = peso(k)
        if p > 0:
            pn = round(v * p, 1)
            lacunas.append({"t": k, "pen": pn, "dif": p < 1})
            total += pn
    total = min(total, cfg.get("lacunas_max_penalidade", 30))
    ajuste -= total

    match = max(0, min(100, round(100 * (0.55 * cobertura + 0.45 * tit) + ajuste)))
    return {"match": match, "cobertura": round(cobertura * 100), "titulo": tit_txt, "skills": skills,
            "lacunas": lacunas, "nivel": nivel, "fraca": fraca, "outras": outras,
            "secoes": {k: bool(v.strip()) for k, v in seg.items()}}


def aderencia(cfg: dict, titulo: str, desc: str) -> int:
    return explicar(cfg, titulo, desc)["match"]

def extrair_metadados(titulo: str, desc: str, tipo_raw: str = "") -> dict:
    """Detecta automaticamente modelo de contratação (CLT/PJ) e faixa salarial na descrição."""
    t_full = f"{titulo} {desc}"
    t_norm = norm(t_full)

    # 1. Contrato
    contrato = ""
    tipo_l = (tipo_raw or "").lower().strip()
    if "legal_entity" in tipo_l or tipo_l == "pj":
        contrato = "PJ"
    elif "effective" in tipo_l or tipo_l == "clt":
        contrato = "CLT"
    elif tipo_l in ("clt / pj", "clt/pj", "pj / clt"):
        contrato = "CLT / PJ"
    elif "associate" in tipo_l or "cooperad" in tipo_l:
        contrato = "Cooperado"
    elif "internship" in tipo_l or "estag" in tipo_l:
        contrato = "Estágio"

    if not contrato:
        tem_pj = bool(re.search(r'\bpj\b|pessoa jur[ií]dica|contrata[cç][aã]o pj|regime pj|modelo pj|prestador de servi[cç]o', t_norm))
        tem_clt = bool(re.search(r'\bclt\b|carteira assinada|regime clt|contrata[cç][aã]o clt|vaga efetiva', t_norm))
        tem_coop = bool(re.search(r'cooperad[ao]|cooperativa', t_norm))

        if tem_pj and tem_clt:
            contrato = "CLT / PJ"
        elif tem_pj:
            contrato = "PJ"
        elif tem_clt:
            contrato = "CLT"
        elif tem_coop:
            contrato = "Cooperado"

    # 2. Salário
    salario = ""
    for m in re.finditer(r'(?:r\$\s*)?(\d{1,2}(?:\.\d{3})*(?:,\d{2})?)\s*(?:mil|k)?\s*(?:a|ate|-|–|—)\s*(?:r\$\s*)?(\d{1,2}(?:\.\d{3})*(?:,\d{2})?)\s*(?:mil|k)?', t_norm):
        ini, fim = max(0, m.start() - 40), min(len(t_norm), m.end() + 40)
        janela = t_norm[ini:fim]
        if any(w in janela for w in ('salario', 'remuneracao', 'faixa', 'r$', 'bolsa', 'pagamento')):
            v1, v2 = m.group(1), m.group(2)
            try:
                n1 = int(v1.split(',')[0].replace('.', ''))
                n2 = int(v2.split(',')[0].replace('.', ''))
                if 1000 <= n1 <= 60000 and 1000 <= n2 <= 60000 and n1 <= n2:
                    salario = f"R$ {v1} a R$ {v2}"
                    break
            except Exception:
                pass

    if not salario:
        m_k = re.search(r'\b(\d{1,2})\s*k\s*(?:a|ate|-|–|—)\s*(\d{1,2})\s*k\b', t_norm)
        if m_k:
            salario = f"R$ {m_k.group(1)}k a {m_k.group(2)}k"

    if not salario:
        m_pontual = re.search(r'(?:salario|remuneracao|bolsa)\s*(?:de|fixo|bruto)?\s*[:=-]?\s*(?:r\$\s*)(\d{1,2}(?:\.\d{3})*(?:,\d{2})?)', t_norm)
        if m_pontual:
            v = m_pontual.group(1)
            try:
                val = int(v.split(',')[0].replace('.', ''))
                if 1100 <= val <= 60000 and val not in (2024, 2025, 2026):
                    salario = f"R$ {v}"
            except Exception:
                pass

    return {"contrato": contrato, "salario": salario}

