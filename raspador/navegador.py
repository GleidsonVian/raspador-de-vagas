"""Edge real controlado por CDP, compartilhado pelas fontes que precisam de navegador (Glassdoor, LinkedIn).

Abre o Edge NORMAL (sem a flag de automacao) com um perfil proprio (perfil_edge/) e conecta o Playwright nele.
Nunca usa o seu Edge do dia a dia nem a sua conta: o perfil e so do raspador. Se um site mostrar uma verificacao
("Confirme que e humano"), quem resolve e VOCE, na janela; o raspador so espera.
"""
import os
import socket
import subprocess
import time

from playwright.sync_api import sync_playwright

from .config import PERFIL_EDGE

PORTA = 9223
EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), "msedge")


def _porta_aberta() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORTA)) == 0


def abrir():
    """Devolve (pw, br, ctx, page). Reaproveita o Edge do raspador se ja estiver aberto."""
    pw = sync_playwright().start()
    try:
        if not _porta_aberta():
            subprocess.Popen([EDGE, f"--remote-debugging-port={PORTA}", f"--user-data-dir={PERFIL_EDGE}",
                              "--no-first-run", "--lang=pt-BR", "about:blank"])
            for _ in range(40):
                if _porta_aberta():
                    break
                time.sleep(0.5)
        br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORTA}")
        ctx = br.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return pw, br, ctx, page
    except Exception:
        pw.stop()
        raise


def fechar(pw, br, ctx, page, manter: bool = False) -> None:
    """Encerra so o Edge do raspador (porta 9223 / perfil_edge). `manter` so desconecta e deixa o Edge aberto."""
    try:
        if not manter:
            try:
                ctx.new_cdp_session(page).send("Browser.close")
            except Exception:
                pass
        br.close()
    except Exception:
        pass
    finally:
        pw.stop()
    if not manter:  # espera a porta fechar: a proxima fonte nao pode conectar num Edge que ainda esta fechando
        for _ in range(20):
            if not _porta_aberta():
                break
            time.sleep(0.5)


def esperar_liberacao(bloqueada, log, parar, rotulo: str, segundos_max: int = 300, passo: float = 2.0) -> bool:
    """Espera uma pessoa resolver a verificacao na janela. `bloqueada()` diz se ainda esta bloqueado.
    True = liberou; False = acabou o tempo ou o usuario cancelou."""
    log(f"{rotulo}: pediu verificacao. Resolva na janela do Edge (espero ate {segundos_max // 60} min)")
    ate = time.time() + segundos_max
    while time.time() < ate:
        if parar is not None and parar.is_set():
            return False
        time.sleep(passo)
        if not bloqueada():
            return True
    return False
