"""Backup do vagas.db: uma copia por dia, mantendo as N mais recentes (padrao 7).

Usa a API de backup do proprio SQLite (consistente mesmo com o painel ou uma busca gravando ao mesmo tempo),
confere a copia (integrity_check + mesma contagem de vagas) e so entao a coloca no lugar. O banco guarda seus
status, notas e vagas ocultas; sem backup, um erro ou disco ruim perde tudo.

Restaurar: feche o painel e copie o arquivo desejado de backups/ por cima de vagas.db.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from . import banco, config

MANTER_PADRAO = 7
PREFIXO = "vagas_"


def pasta() -> Path:
    """Sempre AO LADO do banco copiado (nunca mistura bancos: um banco de teste jamais cai na pasta real)."""
    return Path(banco.DB_PATH).parent / "backups"


def listar() -> list[dict]:
    """Backups existentes, do mais novo para o mais antigo."""
    if not pasta().exists():
        return []
    out = []
    for f in sorted(pasta().glob(f"{PREFIXO}*.db"), reverse=True):
        st = f.stat()
        out.append({"arquivo": f.name, "bytes": st.st_size,
                    "criado_em": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return out


def _contar(caminho: Path) -> tuple[str, int]:
    con = sqlite3.connect(caminho)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0], con.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
    finally:
        con.close()


def fazer_backup(forcar: bool = False, manter: int | None = None) -> Path | None:
    """Cria o backup do dia. Sem `forcar`, devolve None se ja existe um de hoje (ou se nao ha banco).
    Levanta RuntimeError se a copia nao passar na conferencia (a copia ruim e descartada)."""
    origem = Path(banco.DB_PATH)
    if not origem.exists():
        return None
    agora = datetime.now()
    nome = f"{PREFIXO}{agora:%Y%m%d}" + (f"_{agora:%H%M%S}" if forcar else "") + ".db"
    destino = pasta() / nome
    if destino.exists() and not forcar:
        return None
    pasta().mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    src = sqlite3.connect(origem, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        n_origem = src.execute("SELECT COUNT(*) FROM vagas").fetchone()[0]
    finally:
        dst.close()
        src.close()
    try:
        integridade, n_copia = _contar(tmp)
        if integridade != "ok" or n_copia != n_origem:
            raise RuntimeError(f"copia invalida (integridade={integridade}, vagas {n_copia} != {n_origem})")
        tmp.replace(destino)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    _rodizio(manter if manter is not None else config.carregar().get("backup", {}).get("manter", MANTER_PADRAO))
    return destino


def _rodizio(manter: int) -> None:
    arquivos = sorted(pasta().glob(f"{PREFIXO}*.db"), reverse=True)
    for antigo in arquivos[max(1, manter):]:
        antigo.unlink(missing_ok=True)


def garantir_backup_diario(log=None) -> Path | None:
    """Usado no inicio de buscas, do painel e do reavaliar. Nunca derruba quem chamou."""
    try:
        p = fazer_backup()
        if p and log:
            log(f"backup diario criado: {p.name}")
        return p
    except Exception as e:
        if log:
            log(f"aviso: backup falhou ({e})")
        return None
