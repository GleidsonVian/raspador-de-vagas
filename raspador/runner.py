"""Orquestra uma execucao: para cada fonte e termo busca, filtra, pontua e grava no banco.

As fontes rodam em "faixas": as que usam o navegador (Glassdoor, LinkedIn) compartilham UM Edge e UM perfil, entao
rodam uma depois da outra na mesma faixa; cada fonte HTTP (Gupy, Programathor) tem a sua faixa e roda em paralelo
com as demais (sao sites diferentes e nao usam o Edge). Cada faixa usa a propria conexao do SQLite.
Desligue com "paralelo": false no config.json para rodar tudo em sequencia (depuracao).
"""
import html
import json
import os
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import backup, banco, config, dedup, trava
from .fontes import FONTES, Bloqueio
from .pontuacao import explicar, pontuar, extrair_metadados
from .trava import JaRodando

# estado compartilhado com o painel ("ativas": fontes rodando agora, ja que podem ser varias ao mesmo tempo)
ESTADO = {"rodando": False, "fonte": None, "termo": None, "ativas": {}, "feito": 0, "total": 0,
          "novas": 0, "relevantes": 0, "inicio": None, "fim": None, "log": deque(maxlen=400)}
PARAR = threading.Event()
_LOCK = threading.Lock()
_EST = threading.Lock()  # protege o ESTADO contra as faixas paralelas


def _log(msg: str, eco: bool = True):
    linha = f"{datetime.now():%H:%M:%S} {msg}"
    ESTADO["log"].append(linha)
    if eco:
        print(linha, flush=True)


def _est_inc(**incrementos):
    with _EST:
        for k, v in incrementos.items():
            ESTADO[k] += v


def _atividade(nome: str, rotulo: str = "", termo: str | None = None, fim: bool = False):
    """Registra o que cada fonte esta fazendo agora (o painel mostra todas as ativas)."""
    with _EST:
        ativas = ESTADO["ativas"]
        if fim:
            ativas.pop(nome, None)
        else:
            ativas[nome] = {"rotulo": rotulo, "termo": termo}
        ESTADO["fonte"] = " + ".join(a["rotulo"] for a in ativas.values()) or None
        ESTADO["termo"] = termo if not fim else None


def termos_da_fonte(cfg: dict, nome: str) -> list[str]:
    return cfg["fontes"].get(nome, {}).get("termos") or cfg["termos_busca"]


def _avaliar(cfg: dict, v: dict) -> bool:
    """Pontua a vaga (preenche score, match, motivo, contrato e salario). Retorna True se for relevante."""
    sc = pontuar(cfg, v["titulo"], v["descricao"])
    v["score"] = -1 if sc is None else sc
    if sc:
        ex = explicar(cfg, v["titulo"], v["descricao"])
        v["match"], v["motivo"] = ex["match"], json.dumps(ex, ensure_ascii=False)
    else:
        v["match"], v["motivo"] = 0, ""
    meta = extrair_metadados(v["titulo"], v.get("descricao", ""), v.get("tipo_raw", ""))
    v["contrato"], v["salario"] = meta["contrato"], meta["salario"]
    return sc is not None and sc >= cfg["pontuacao_minima"]


def _registrar(con, chaves: dict, trava_chaves: threading.Lock, v: dict) -> None:
    """Grava a vaga e guarda a chave (empresa, titulo) para nao baixar duplicadas de outras fontes."""
    banco.inserir(con, v)
    k = dedup.chave_vaga(v["titulo"], v["empresa"])
    if dedup._valida(k):
        with trava_chaves:
            chaves.setdefault(k, v["id"])


def _alertas(lista, lidas, vazias, desc_tot, desc_vazias, avisos) -> str | None:
    """Sinais de que o site mudou de layout ou bloqueou em silencio."""
    a = list(avisos)
    if lidas == 0 and len(lista) >= 2:
        a.append(f"nenhuma vaga lida em {len(lista)} buscas (layout mudou ou acesso bloqueado?)")
    elif len(lista) >= 4 and vazias / len(lista) > 0.6:
        a.append(f"{vazias} de {len(lista)} buscas vieram vazias")
    if desc_tot >= 5 and desc_vazias / desc_tot > 0.5:
        a.append(f"{desc_vazias} de {desc_tot} descricoes vieram vazias (endpoint mudou?)")
    return "; ".join(dict.fromkeys(a)) or None


def _usa_navegador(nome: str, cfg: dict) -> bool:
    f = getattr(FONTES.get(nome), "usa_navegador", None)
    return bool(f(cfg)) if f else False


def _faixas(fontes: list[str], cfg: dict) -> list[list[str]]:
    """[[fontes com navegador, em ordem], [gupy], [programathor]...]. Sem paralelismo: uma faixa so."""
    if not cfg.get("paralelo", True):
        return [list(fontes)]
    nav = [n for n in fontes if _usa_navegador(n, cfg)]
    return ([nav] if nav else []) + [[n] for n in fontes if n not in nav]


def executar(fontes: list[str] | None = None, termos: list[str] | None = None, dias: int | None = None,
             manter_aberto: bool = False) -> list[dict]:
    """Roda sincronamente (com trava entre processos). Retorna as vagas novas relevantes.
    Levanta JaRodando se outra execucao estiver em andamento. Use iniciar() para rodar em segundo plano."""
    with trava.trava():
        return _executar(fontes, termos, dias, manter_aberto)


def _rodar_fonte(nome, cfg, termos, dias, manter_aberto, chaves, trava_chaves) -> list[dict]:
    """Uma fonte inteira (todos os termos). Usa a PROPRIA conexao do banco: pode rodar em paralelo com outras."""
    cls = FONTES.get(nome)
    if not cls:
        _log(f"fonte desconhecida: {nome}")
        return []
    rotulo = cls.ROTULO
    lista = termos or termos_da_fonte(cfg, nome)
    relevantes: list[dict] = []
    con = banco.conectar()
    try:
        pausa = banco.pausa_ativa(con, nome)
        if pausa:
            _log(f"[{rotulo}] em pausa ate {pausa[0][11:16]} ({pausa[1]}); pulando para nao piorar o bloqueio")
            _est_inc(feito=len(lista))
            return []
        ex = con.execute("INSERT INTO execucoes(fonte,inicio,termos) VALUES(?,?,?)", (nome, banco.agora(), len(lista)))
        exec_id, lidas, novas, rel, erro = ex.lastrowid, 0, 0, 0, None
        vazias = desc_tot = desc_vazias = 0
        avisos: list[str] = []
        con.commit()
        _atividade(nome, rotulo, None)
        _log(f"[{rotulo}] == {len(lista)} termos ==")
        try:
            with cls(cfg, _log, PARAR, manter_aberto=manter_aberto) as fonte:
                for termo in lista:
                    if PARAR.is_set():
                        break
                    _atividade(nome, rotulo, termo)
                    _log(f"[{rotulo}] buscando: {termo}")
                    try:
                        vagas = fonte.buscar(termo)
                    except Bloqueio:
                        raise
                    except Exception as e:
                        _log(f"[{rotulo}]   erro na busca: {str(e)[:120]}")
                        _est_inc(feito=1)
                        continue
                    lidas += len(vagas)
                    vazias += 0 if vagas else 1
                    _log(f"[{rotulo}]   {len(vagas)} vagas lidas")
                    for v in vagas:
                        if PARAR.is_set():
                            break
                        if v["idade_dias"] is not None and v["idade_dias"] > dias:
                            continue
                        v["fonte"], v["termo"] = nome, termo
                        if banco.ja_conhecida(con, v["id"]):
                            continue
                        k = dedup.chave_vaga(v["titulo"], v["empresa"])
                        with trava_chaves:
                            original = chaves.get(k) if dedup._valida(k) else None
                        if original:
                            # a mesma vaga ja existe (outra fonte ou republicada): nao gasta requisicao
                            v.update(descricao="", score=0, match=0, motivo="", duplicada_de=original)
                            _registrar(con, chaves, trava_chaves, v)
                            novas += 1
                            continue
                        decisao = fonte.vale_descricao(v) if hasattr(fonte, "vale_descricao") else "sim"
                        if decisao == "depois":
                            continue  # limite da execucao: fica para a proxima (nao registra)
                        if decisao == "nao":
                            v.update(descricao="", score=0, match=0, motivo="")  # titulo irrelevante: registra e segue
                            _registrar(con, chaves, trava_chaves, v)
                            novas += 1
                            continue
                        v["descricao"] = fonte.descricao(v)
                        desc_tot += 1
                        desc_vazias += 0 if v["descricao"] else 1
                        ok = _avaliar(cfg, v)
                        _registrar(con, chaves, trava_chaves, v)
                        novas += 1
                        if ok:
                            rel += 1
                            relevantes.append(v)
                            _est_inc(relevantes=1)
                            _log(f"[{rotulo}]   + {v['match']:>3}%  {v['titulo']} | {v['empresa']}")
                        if nome == "glassdoor":
                            time.sleep(random.uniform(1, 2.5))
                    _est_inc(feito=1)
                    time.sleep(random.uniform(*cfg["pausa_entre_buscas_seg"]))
                avisos = list(getattr(fonte, "avisos", []))
        except Bloqueio as e:
            ate = banco.pausar(con, nome, str(e), cfg["fontes"].get(nome, {}).get("pausa_base_horas", 3), e.retry_after)
            erro = f"{e} - fonte em pausa ate {ate[11:16]}"
            _log(f"[{rotulo}] {erro}")
        except Exception as e:
            erro = f"{type(e).__name__}: {str(e)[:150]}"
            _log(f"[{rotulo}] falhou: {erro}")
        if not erro and not PARAR.is_set():
            banco.retomar(con, nome)  # terminou sem bloqueio: zera o backoff
        alerta = None if (erro or PARAR.is_set()) else _alertas(
            lista, lidas, vazias if getattr(cls, "ALERTAR_BUSCAS_VAZIAS", True) else 0, desc_tot, desc_vazias, avisos)
        if alerta:
            _log(f"[{rotulo}] ALERTA: {alerta}")
        con.execute("UPDATE execucoes SET fim=?, lidas=?, novas=?, relevantes=?, erro=?, alerta=?, desc_vazias=? WHERE id=?",
                    (banco.agora(), lidas, novas, rel, erro, alerta, desc_vazias, exec_id))
        con.commit()
        _est_inc(novas=novas)
        return relevantes
    finally:
        _atividade(nome, fim=True)
        con.close()


def _rodar_faixa(nomes, cfg, termos, dias, manter_aberto, chaves, trava_chaves) -> list[dict]:
    """Fontes de uma faixa, uma depois da outra. Um erro numa fonte nao derruba as demais."""
    out: list[dict] = []
    for nome in nomes:
        if PARAR.is_set():
            break
        try:
            out += _rodar_fonte(nome, cfg, termos, dias, manter_aberto, chaves, trava_chaves)
        except Exception as e:  # seguranca: _rodar_fonte ja trata os erros esperados
            _log(f"[{nome}] erro inesperado: {type(e).__name__}: {str(e)[:150]}")
    return out


def _executar(fontes, termos, dias, manter_aberto) -> list[dict]:
    backup.garantir_backup_diario(_log)  # antes de gravar qualquer coisa
    cfg = config.carregar()
    fontes = fontes or [n for n, c in cfg["fontes"].items() if c.get("ativa", True)]
    dias = dias if dias is not None else cfg["apenas_ultimos_dias"]
    cfg["apenas_ultimos_dias"] = dias
    con = banco.conectar()
    relevantes: list[dict] = []
    chaves = dedup.carregar_chaves(con)
    trava_chaves = threading.Lock()
    PARAR.clear()
    ESTADO.update(rodando=True, fonte=None, termo=None, ativas={}, feito=0, novas=0, relevantes=0,
                  inicio=banco.agora(), fim=None, total=sum(len(termos or termos_da_fonte(cfg, f)) for f in fontes))
    try:
        faixas = _faixas(fontes, cfg)
        if len(faixas) > 1:
            _log("faixas em paralelo: " + " | ".join(" > ".join(f) for f in faixas))
        with ThreadPoolExecutor(max_workers=max(1, len(faixas)), thread_name_prefix="faixa") as pool:
            futuros = [pool.submit(_rodar_faixa, f, cfg, termos, dias, manter_aberto, chaves, trava_chaves) for f in faixas]
            for fu in futuros:
                relevantes += fu.result()
        _log(f"=== {len(relevantes)} vagas novas relevantes ===")
        if not relevantes:
            _log("Nenhuma vaga nova desde a ultima execucao (as ja vistas ficam no banco).")
    finally:
        ESTADO.update(rodando=False, fim=banco.agora(), fonte=None, termo=None, ativas={})
        try:
            dedup.atualizar_duplicadas(con)
        except Exception as e:
            _log(f"aviso: deduplicacao falhou ({e})")
        con.close()
    return relevantes


def iniciar(fontes=None, termos=None) -> bool:
    """Dispara em segundo plano (painel). False se ja houver uma execucao em andamento (neste ou em outro processo)."""
    if trava.ocupado() or not _LOCK.acquire(blocking=False):
        return False

    def alvo():
        try:
            executar(fontes, termos)
        except JaRodando as e:
            _log(str(e))
        except Exception as e:  # nunca deixa a thread morrer sem avisar
            _log(f"erro inesperado: {e}")
        finally:
            _LOCK.release()
    threading.Thread(target=alvo, daemon=True, name="execucao").start()
    return True


def relatorio_html(vagas: list[dict]):
    """Relatorio simples para execucoes agendadas (o painel mostra o mesmo, com mais recursos)."""
    if not vagas:
        return None
    vagas = sorted(vagas, key=lambda v: (-v["match"], -v["score"]))
    linhas = "".join(
        f"<tr><td><b>{v['match']}%</b></td><td><a href='{html.escape(v['link'])}'>{html.escape(v['titulo'])}</a></td>"
        f"<td>{html.escape(v['empresa'])}</td><td>{html.escape(v['fonte'])}</td>"
        f"<td>{'Sim' if v.get('easy') else 'Não'}</td></tr>" for v in vagas)
    p = config.BASE / f"novas_{datetime.now():%Y%m%d_%H%M}.html"
    p.write_text(f"<meta charset=utf-8><h2>{len(vagas)} vagas novas</h2><table border=1 cellpadding=6>"
                 f"<tr><th>% match</th><th>vaga</th><th>empresa</th><th>fonte</th><th>Candidatura Rápida</th></tr>{linhas}</table>",
                 encoding="utf-8")
    return p


def abrir_arquivo(p):
    try:
        os.startfile(p)
    except Exception:
        pass


def reavaliar() -> int:
    """Recalcula score, % de match e motivo de TODAS as vagas salvas com as regras atuais do config.json.
    Nao mexe em status nem em exclusoes."""
    cfg = config.carregar()
    backup.garantir_backup_diario(_log)
    con = banco.conectar()
    try:
        linhas = con.execute("SELECT id,titulo,descricao,tipo_raw,contrato FROM vagas").fetchall()
        for r in linhas:
            v = {"titulo": r["titulo"], "descricao": r["descricao"] or "", "tipo_raw": r["tipo_raw"] or ""}
            _avaliar(cfg, v)
            con.execute("UPDATE vagas SET score=?, match=?, motivo=?, contrato=?, salario=? WHERE id=?",
                        (v["score"], v["match"], v["motivo"], v["contrato"] or (r["contrato"] or ""), v["salario"], r["id"]))
        con.commit()
        try:
            dedup.atualizar_duplicadas(con)
        except Exception as e:
            _log(f"aviso: deduplicacao falhou ({e})")
        return len(linhas)
    finally:
        con.close()
