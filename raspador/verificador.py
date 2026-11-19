"""Verificador de vagas expiradas/encerradas (Ghost Job Detector)."""
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PADROES_ENCERRADA = (
    "vaga encerrada",
    "nao esta mais disponivel",
    "não está mais disponível",
    "candidaturas encerradas",
    "job has expired",
    "no longer available",
    "vaga finalizada",
    "processo seletivo encerrado",
    "esta vaga foi finalizada",
    "404 not found",
    "pagina nao encontrada",
    "página não encontrada",
)


def checar_vaga(link: str) -> bool:
    """Retorna True se a vaga parecer encerrada/expirada."""
    if not link or not link.startswith("http"):
        return False
    try:
        r = requests.get(link, headers=HEADERS, timeout=7, allow_redirects=True)
        if r.status_code in (404, 410):
            return True
        texto = r.text.lower()
        if any(p in texto for p in PADROES_ENCERRADA):
            return True
        return False
    except Exception:
        return False


def verificar_ativas(con, limite: int = 35) -> dict:
    """Verifica as vagas ativas e marca encerrada=1 nas que já expiraram."""
    linhas = con.execute(
        "SELECT id, link, titulo, fonte FROM vagas WHERE excluida=0 AND (encerrada IS NULL OR encerrada=0) AND link != '' ORDER BY visto_em ASC LIMIT ?",
        (limite,)
    ).fetchall()

    encerradas = 0
    for r in linhas:
        if checar_vaga(r["link"]):
            con.execute("UPDATE vagas SET encerrada=1 WHERE id=?", (r["id"],))
            encerradas += 1

    con.commit()
    return {"verificadas": len(linhas), "encerradas": encerradas}
