"""SQLite: vagas + historico de execucoes. Migra o banco antigo sem perder dados."""
import sqlite3
from datetime import datetime, timedelta

from .config import DB_PATH

COLUNAS_VAGAS = {
    # coluna: definicao (usada tanto no CREATE quanto no ALTER de bancos antigos)
    "fonte": "TEXT DEFAULT 'glassdoor'",
    "status": "TEXT DEFAULT 'nova'",       # nova | interessado | candidatado | descartado
    "excluida": "INTEGER DEFAULT 0",       # 1 = oculta (o id continua guardado: nao volta a ser coletada)
    "match": "INTEGER DEFAULT 0",
    "notificada": "INTEGER DEFAULT 0",
    "motivo": "TEXT",                      # JSON com o detalhe do calculo do match (pontuacao.explicar)
}
COLUNAS_EXEC = {"alerta": "TEXT", "desc_vazias": "INTEGER DEFAULT 0"}


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS vagas(
        id TEXT PRIMARY KEY, titulo TEXT, empresa TEXT, nota REAL, idade_dias INTEGER,
        local TEXT, easy INTEGER, link TEXT, termo TEXT, score INTEGER,
        descricao TEXT, visto_em TEXT)""")
    existentes = {r["name"] for r in con.execute("PRAGMA table_info(vagas)")}
    for col, ddl in COLUNAS_VAGAS.items():
        if col not in existentes:
            con.execute(f"ALTER TABLE vagas ADD COLUMN {col} {ddl}")
    con.execute("""CREATE TABLE IF NOT EXISTS execucoes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, fonte TEXT, inicio TEXT, fim TEXT,
        termos INTEGER, lidas INTEGER DEFAULT 0, novas INTEGER DEFAULT 0,
        relevantes INTEGER DEFAULT 0, erro TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pausas(
        fonte TEXT PRIMARY KEY, ate TEXT, falhas INTEGER DEFAULT 0, motivo TEXT)""")
    ex_cols = {r["name"] for r in con.execute("PRAGMA table_info(execucoes)")}
    for col, ddl in COLUNAS_EXEC.items():
        if col not in ex_cols:
            con.execute(f"ALTER TABLE execucoes ADD COLUMN {col} {ddl}")
    con.commit()
    return con


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pausa_ativa(con, fonte: str):
    """(ate, motivo) se a fonte esta em pausa por bloqueio do site; senao None."""
    r = con.execute("SELECT ate, motivo FROM pausas WHERE fonte=?", (fonte,)).fetchone()
    if r and r["ate"] > datetime.now().isoformat(timespec="seconds"):
        return r["ate"], r["motivo"]
    return None


def pausar(con, fonte: str, motivo: str, base_horas: float = 3, retry_after: int | None = None) -> str:
    """Bloqueio do site: pausa a fonte com backoff (base, 2x, 4x... ate 24h) e devolve ate quando."""
    r = con.execute("SELECT falhas FROM pausas WHERE fonte=?", (fonte,)).fetchone()
    falhas = (r["falhas"] if r else 0) + 1
    horas = min(24.0, base_horas * 2 ** (falhas - 1))
    if retry_after:
        horas = max(horas, retry_after / 3600)
    ate = (datetime.now() + timedelta(hours=horas)).isoformat(timespec="seconds")
    con.execute("INSERT INTO pausas(fonte,ate,falhas,motivo) VALUES(?,?,?,?) "
                "ON CONFLICT(fonte) DO UPDATE SET ate=excluded.ate, falhas=excluded.falhas, motivo=excluded.motivo",
                (fonte, ate, falhas, motivo))
    con.commit()
    return ate


def retomar(con, fonte: str) -> None:
    """Execucao concluida sem bloqueio: zera o backoff."""
    con.execute("DELETE FROM pausas WHERE fonte=?", (fonte,))
    con.commit()


def ja_conhecida(con, vid: str) -> bool:
    """Inclui as excluidas: assim excluir uma vaga nao a faz voltar na proxima busca."""
    return con.execute("SELECT 1 FROM vagas WHERE id=?", (vid,)).fetchone() is not None


def inserir(con, v: dict) -> None:
    con.execute(
        "INSERT INTO vagas(id,fonte,titulo,empresa,nota,idade_dias,local,easy,link,termo,score,descricao,visto_em,match,motivo,status,excluida)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'nova',0)",
        (v["id"], v["fonte"], v["titulo"], v["empresa"], v.get("nota"), v.get("idade_dias"), v.get("local", ""),
         int(bool(v.get("easy"))), v.get("link", ""), v.get("termo", ""), v["score"], v.get("descricao", ""),
         agora(), v["match"], v.get("motivo", "")))
    con.commit()
