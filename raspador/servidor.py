"""Painel web local: lista de vagas, status, exclusao, execucao manual e configuracao.
Sobe em http://127.0.0.1:8765 (so acessivel nesta maquina)."""
import json
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import analytics, backup, banco, checklist, config, dedup, runner, simulador, sugestoes, trava, verificador
from .fontes import FONTES

PORTA = int(os.environ.get("RASPADOR_PORTA", 8765))
WEB = Path(__file__).parent / "web"
STATUS_VALIDOS = {"nova", "interessado", "candidatado", "descartado"}
HOSTS_OK = {f"127.0.0.1:{PORTA}", f"localhost:{PORTA}"}

app = FastAPI(title="Raspador de vagas", docs_url=None, redoc_url=None)


@app.middleware("http")
async def protecao(request: Request, call_next):
    # contra DNS rebinding: so aceita o proprio host local
    if request.headers.get("host", "") not in HOSTS_OK:
        return JSONResponse({"erro": "host nao permitido"}, status_code=403)
    # contra CSRF: escrita exige um header customizado (outros sites nao conseguem enviar sem preflight)
    if request.method != "GET" and request.headers.get("x-painel") != "1":
        return JSONResponse({"erro": "requisicao nao autorizada"}, status_code=403)
    return await call_next(request)


def _linha(r) -> dict:
    d = dict(r)
    d["capturada_em"] = d.pop("visto_em")
    return d


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/api/vagas")
def listar(fonte: str = "", status: str = "", q: str = "", contrato: str = "", min_match: int = 0,
           excluidas: bool = False, todas: bool = False, duplicadas: bool = False, limite: int = 500):
    """todas=False mostra so as relevantes; duplicadas=False oculta vagas repetidas."""
    minimo = config.carregar()["pontuacao_minima"]
    sql = ("SELECT id,fonte,titulo,empresa,nota,idade_dias,local,easy,link,termo,score,match,visto_em,status,excluida,contrato,salario,duplicada_de,encerrada "
           "FROM vagas WHERE excluida=? AND match>=?")
    par: list = [1 if excluidas else 0, min_match]
    if not duplicadas:
        sql += " AND (duplicada_de IS NULL OR duplicada_de = '')"
    if not todas:
        sql += " AND score>=?"
        par.append(minimo)
    if fonte:
        sql += " AND fonte=?"
        par.append(fonte)
    if status:
        sql += " AND status=?"
        par.append(status)
    if contrato:
        sql += " AND contrato LIKE ?"
        par.append(f"%{contrato}%")
    if q:
        sql += " AND (titulo LIKE ? OR empresa LIKE ?)"
        par += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY match DESC, visto_em DESC LIMIT ?"
    par.append(max(1, min(limite, 2000)))
    con = banco.conectar()
    try:
        return [_linha(r) for r in con.execute(sql, par)]
    finally:
        con.close()


@app.get("/api/vagas/{vid:path}/descricao")
def descricao(vid: str):
    con = banco.conectar()
    try:
        r = con.execute("SELECT descricao, motivo FROM vagas WHERE id=?", (vid,)).fetchone()
    finally:
        con.close()
    if not r:
        raise HTTPException(404, "vaga nao encontrada")
    try:
        motivo = json.loads(r["motivo"]) if r["motivo"] else None
    except ValueError:
        motivo = None
    return {"descricao": r["descricao"] or "", "motivo": motivo}


@app.get("/api/vagas/{vid:path}/checklist")
def checklist_da_vaga(vid: str):
    """O que voce tem, o que falta e o que destacar para esta vaga (sem IA; ver raspador/checklist.py)."""
    con = banco.conectar()
    try:
        r = con.execute("SELECT titulo,empresa,fonte,descricao,contrato,salario,easy,link,idade_dias,local "
                        "FROM vagas WHERE id=?", (vid,)).fetchone()
    finally:
        con.close()
    if not r:
        raise HTTPException(404, "vaga nao encontrada")
    return checklist.montar(config.carregar(), dict(r))


def _relevantes(con, cfg: dict) -> list[dict]:
    """Vagas relevantes, abertas e unicas (as mesmas das estatisticas)."""
    return [dict(r) for r in con.execute(
        "SELECT id,titulo,empresa,fonte,link,descricao,match FROM vagas WHERE excluida=0 AND encerrada=0 "
        "AND (duplicada_de IS NULL OR duplicada_de='') AND score>=? AND COALESCE(descricao,'')!=''", (cfg["pontuacao_minima"],))]


@app.get("/api/simulador/opcoes")
def simulador_opcoes():
    cfg = config.carregar()
    con = banco.conectar()
    try:
        linhas = _relevantes(con, cfg)
    finally:
        con.close()
    return {"opcoes": simulador.opcoes(cfg, linhas), "total": len(linhas)}


class SimularIn(BaseModel):
    tecnologias: list[str]


@app.post("/api/simulador")
def simular(corpo: SimularIn):
    """E se eu aprendesse X? Nada e gravado: so recalcula o match em memoria."""
    cfg = config.carregar()
    con = banco.conectar()
    try:
        linhas = _relevantes(con, cfg)
    finally:
        con.close()
    try:
        return simulador.simular(cfg, linhas, corpo.tecnologias[:10])
    except ValueError as e:
        raise HTTPException(400, str(e))


def _linhas_status(con, cfg: dict) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT status, descricao FROM vagas WHERE excluida=0 AND score>=? AND COALESCE(descricao,'')!='' "
        "AND (duplicada_de IS NULL OR duplicada_de='')", (cfg["pontuacao_minima"],))]


@app.get("/api/sugestoes/lacunas")
def sugestoes_lacunas():
    cfg = config.carregar()
    con = banco.conectar()
    try:
        return sugestoes.sugerir(cfg, _linhas_status(con, cfg))
    finally:
        con.close()


class LacunaIn(BaseModel):
    tech: str
    pen: int = 8


@app.post("/api/lacunas")
def adicionar_lacuna(corpo: LacunaIn):
    """Adiciona a tecnologia as lacunas (desconto no match) e recalcula os matches se nao houver busca em andamento."""
    cfg = config.carregar()
    con = banco.conectar()
    try:
        novo, termo, pen = sugestoes.aplicar(cfg, corpo.tech, corpo.pen, _linhas_status(con, cfg))
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    config.salvar(novo)
    reavaliadas = None if (runner.ESTADO["rodando"] or trava.ocupado()) else runner.reavaliar()
    return {"termo": termo, "pen": pen, "reavaliadas": reavaliadas}


class IgnorarIn(BaseModel):
    tech: str


@app.post("/api/lacunas/ignorar")
def ignorar_lacuna(corpo: IgnorarIn):
    try:
        config.salvar(sugestoes.ignorar(config.carregar(), corpo.tech))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class StatusIn(BaseModel):
    status: str


@app.post("/api/vagas/{vid:path}/status")
def mudar_status(vid: str, corpo: StatusIn):
    if corpo.status not in STATUS_VALIDOS:
        raise HTTPException(400, "status invalido")
    con = banco.conectar()
    try:
        n = con.execute("UPDATE vagas SET status=? WHERE id=?", (corpo.status, vid)).rowcount
        con.commit()
    finally:
        con.close()
    if not n:
        raise HTTPException(404, "vaga nao encontrada")
    return {"ok": True}


class ExcluirIn(BaseModel):
    definitivo: bool = False


@app.post("/api/vagas/{vid:path}/excluir")
def excluir(vid: str, corpo: ExcluirIn):
    """Padrao: oculta (excluida=1) e MANTEM o id, para a vaga nao ser coletada de novo.
    definitivo=True apaga a linha; se o site ainda listar a vaga, ela pode voltar como nova."""
    con = banco.conectar()
    try:
        if corpo.definitivo:
            n = con.execute("DELETE FROM vagas WHERE id=?", (vid,)).rowcount
        else:
            n = con.execute("UPDATE vagas SET excluida=1 WHERE id=?", (vid,)).rowcount
        con.commit()
    finally:
        con.close()
    if not n:
        raise HTTPException(404, "vaga nao encontrada")
    return {"ok": True}


@app.post("/api/vagas/{vid:path}/restaurar")
def restaurar(vid: str):
    con = banco.conectar()
    try:
        n = con.execute("UPDATE vagas SET excluida=0 WHERE id=?", (vid,)).rowcount
        con.commit()
    finally:
        con.close()
    if not n:
        raise HTTPException(404, "vaga nao encontrada")
    return {"ok": True}


@app.get("/api/resumo")
def resumo():
    minimo = config.carregar()["pontuacao_minima"]
    con = banco.conectar()
    try:
        por_status = {r["status"]: r["n"] for r in con.execute(
            "SELECT status, COUNT(*) n FROM vagas WHERE excluida=0 AND score>=? GROUP BY status", (minimo,))}
        por_fonte = {r["fonte"]: r["n"] for r in con.execute(
            "SELECT fonte, COUNT(*) n FROM vagas WHERE excluida=0 AND score>=? GROUP BY fonte", (minimo,))}
        total = con.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
        exc = con.execute("SELECT COUNT(*) FROM vagas WHERE excluida=1").fetchone()[0]
    finally:
        con.close()
    return {"por_status": por_status, "por_fonte": por_fonte, "lidas_total": total, "excluidas": exc,
            "fontes": {n: c.ROTULO for n, c in FONTES.items()}}


@app.get("/api/analytics")
def dados_analytics():
    con = banco.conectar()
    try:
        cfg = config.carregar()
        return analytics.obter_analytics(con, cfg["pontuacao_minima"], cfg)
    finally:
        con.close()


@app.post("/api/pausa/{fonte}/limpar")
def limpar_pausa(fonte: str):
    """Encerra a pausa agora. O contador de falhas e mantido: se bloquear de novo, a proxima pausa e mais longa."""
    if fonte not in FONTES:
        raise HTTPException(404, "fonte desconhecida")
    con = banco.conectar()
    try:
        banco.encerrar_pausa(con, fonte)
    finally:
        con.close()
    return {"ok": True}


def _msg_erro(erro: str) -> str:
    """O erro gravado na execucao termina com '- fonte em pausa ate HH:MM'. Se a pausa ja acabou, isso engana."""
    base, sep, _ = erro.partition(" - fonte em pausa ate")
    return base + " (a pausa já terminou; tenta de novo na próxima execução)" if sep else erro


@app.get("/api/saude")
def saude():
    """Estado de cada fonte: ok / alerta (suspeita de layout mudado) / erro / nunca rodou."""
    con = banco.conectar()
    out = {}
    try:
        for nome, cls in FONTES.items():
            ult = con.execute("SELECT * FROM execucoes WHERE fonte=? AND fim IS NOT NULL ORDER BY id DESC LIMIT 1",
                              (nome,)).fetchone()
            ok = con.execute("SELECT fim FROM execucoes WHERE fonte=? AND fim IS NOT NULL AND erro IS NULL "
                             "AND alerta IS NULL ORDER BY id DESC LIMIT 1", (nome,)).fetchone()
            pausa = banco.pausa_ativa(con, nome)
            if pausa:
                out[nome] = {"rotulo": cls.ROTULO, "estado": "pausa", "ultimo_ok": ok["fim"] if ok else None,
                             "mensagem": f"em pausa ate {pausa[0][11:16]} por limite de acessos do site ({pausa[1]})",
                             "quando": ult["fim"] if ult else None}
                continue
            if not ult:
                # fonte que ja coletou antes do historico de execucoes existir: usa a data da ultima vaga
                ant = con.execute("SELECT MAX(visto_em) m, COUNT(*) n FROM vagas WHERE fonte=?", (nome,)).fetchone()
                if ant["n"]:
                    estado, msg, quando = "ok", f"{ant['n']} vagas coletadas (antes do historico de execucoes)", ant["m"]
                else:
                    estado, msg, quando = "nunca", "ainda nao rodou", None
            elif ult["erro"]:
                estado, msg, quando = "erro", _msg_erro(ult["erro"]), ult["fim"]
            elif ult["alerta"]:
                estado, msg, quando = "alerta", ult["alerta"], ult["fim"]
            else:
                estado, msg, quando = "ok", f"{ult['lidas']} vagas lidas", ult["fim"]
            out[nome] = {"rotulo": cls.ROTULO, "estado": estado, "mensagem": msg, "quando": quando,
                         "ultimo_ok": ok["fim"] if ok else None}
    finally:
        con.close()
    return out


@app.get("/api/backups")
def listar_backups():
    return {"pasta": "backups/", "manter": config.carregar().get("backup", {}).get("manter", backup.MANTER_PADRAO),
            "arquivos": backup.listar()}


@app.post("/api/backups")
def novo_backup():
    """Backup manual (alem do diario). Seguro durante uma busca: usa a API de backup do SQLite."""
    try:
        p = backup.fazer_backup(forcar=True)
    except Exception as e:
        raise HTTPException(500, f"backup falhou: {e}")
    if not p:
        raise HTTPException(404, "banco de vagas ainda nao existe")
    return {"arquivo": p.name, "arquivos": backup.listar()}


@app.get("/api/estado")
def estado():
    e = dict(runner.ESTADO)
    e["log"] = list(e["log"])[-120:]
    return e


class RodarIn(BaseModel):
    fontes: list[str] | None = None


@app.post("/api/rodar")
def rodar(corpo: RodarIn):
    fontes = [f for f in (corpo.fontes or []) if f in FONTES] or None
    if not runner.iniciar(fontes):
        raise HTTPException(409, "ja existe uma execucao em andamento")
    return {"ok": True}


@app.post("/api/reavaliar")
def reavaliar():
    if runner.ESTADO["rodando"]:
        raise HTTPException(409, "aguarde a execucao terminar")
    return {"vagas": runner.reavaliar()}


@app.post("/api/vagas/verificar-ativas")
def verificar_ativas():
    if runner.ESTADO["rodando"] or trava.ocupado():
        raise HTTPException(409, "aguarde a execucao terminar")
    con = banco.conectar()
    try:
        return verificador.verificar_ativas(con)
    finally:
        con.close()


@app.post("/api/dedup")
def rodar_dedup():
    con = banco.conectar()
    try:
        return {"duplicadas": dedup.atualizar_duplicadas(con)}
    finally:
        con.close()


@app.post("/api/parar")
def parar():
    runner.PARAR.set()
    return {"ok": True}


@app.get("/api/execucoes")
def execucoes():
    con = banco.conectar()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM execucoes ORDER BY id DESC LIMIT 30")]
    finally:
        con.close()


class ConfigIn(BaseModel):
    termos_busca: list[str]
    termos_linkedin: list[str]
    termos_gupy: list[str] = []
    termos_programathor: list[str] = []
    pontuacao_minima: int
    apenas_ultimos_dias: int
    glassdoor_ativa: bool = True
    linkedin_ativa: bool = True
    gupy_ativa: bool = True
    programathor_ativa: bool = True


def _limpa(lst: list[str]) -> list[str]:
    vistos, out = set(), []
    for t in lst:
        t = " ".join(t.split())
        if t and t.lower() not in vistos and len(t) <= 80:
            vistos.add(t.lower())
            out.append(t)
    return out[:60]


@app.get("/api/config")
def ler_config():
    c = config.carregar()
    return {"termos_busca": c["termos_busca"],
            "termos_linkedin": c["fontes"]["linkedin"].get("termos", []),
            "termos_gupy": c["fontes"].get("gupy", {}).get("termos") or ["rpa", "desenvolvedor rpa", "analista de automacao", "automacao python", "n8n", "uipath"],
            "termos_programathor": c["fontes"].get("programathor", {}).get("termos") or ["selenium", "n8n", "python", "robotframework", "autoit"],
            "pontuacao_minima": c["pontuacao_minima"], "apenas_ultimos_dias": c["apenas_ultimos_dias"],
            "glassdoor_ativa": c["fontes"]["glassdoor"].get("ativa", True),
            "linkedin_ativa": c["fontes"]["linkedin"].get("ativa", True),
            "gupy_ativa": c["fontes"].get("gupy", {}).get("ativa", True),
            "programathor_ativa": c["fontes"].get("programathor", {}).get("ativa", True)}


@app.put("/api/config")
def salvar_config(corpo: ConfigIn):
    c = config.carregar()
    c["termos_busca"] = _limpa(corpo.termos_busca)
    c["fontes"]["linkedin"]["termos"] = _limpa(corpo.termos_linkedin)
    c["pontuacao_minima"] = max(0, min(corpo.pontuacao_minima, 100))
    c["apenas_ultimos_dias"] = max(1, min(corpo.apenas_ultimos_dias, 90))
    c["fontes"]["glassdoor"]["ativa"] = corpo.glassdoor_ativa
    c["fontes"]["linkedin"]["ativa"] = corpo.linkedin_ativa
    if "gupy" not in c["fontes"]:
        c["fontes"]["gupy"] = {}
    c["fontes"]["gupy"]["termos"] = _limpa(corpo.termos_gupy)
    c["fontes"]["gupy"]["ativa"] = corpo.gupy_ativa
    if "programathor" not in c["fontes"]:
        c["fontes"]["programathor"] = {}
    c["fontes"]["programathor"]["termos"] = _limpa(corpo.termos_programathor)
    c["fontes"]["programathor"]["ativa"] = corpo.programathor_ativa
    config.salvar(c)
    return {"ok": True}


def main(abrir: bool = True):
    backup.garantir_backup_diario()  # copia do dia ANTES da migracao
    banco.conectar().close()  # garante a migracao antes de servir
    if abrir:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORTA}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORTA, log_level="warning")


if __name__ == "__main__":
    main()
