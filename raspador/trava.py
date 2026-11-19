"""Trava entre processos: impede que o painel, a tarefa agendada e o terminal rodem ao mesmo tempo
(eles brigariam pelo mesmo Edge/porta 9223 e pelo mesmo banco)."""
import ctypes
import os
import time
from contextlib import contextmanager

from .config import BASE

ARQ = BASE / "raspador.lock"
MAX_IDADE = 4 * 3600  # lock mais velho que isso e considerado abandonado


class JaRodando(Exception):
    pass


def _vivo(pid: int) -> bool:
    """O processo existe e ainda esta ativo? (NAO usa os.kill: no Windows ele mataria o processo)"""
    if pid <= 0:
        return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        codigo = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(codigo))) and codigo.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def ocupado() -> bool:
    try:
        pid, ts = ARQ.read_text().split()
        pid, ts = int(pid), float(ts)
    except (FileNotFoundError, ValueError, OSError):
        return False
    return (time.time() - ts) <= MAX_IDADE and _vivo(pid)


@contextmanager
def trava():
    msg = "Ja existe uma execucao em andamento (painel, tarefa agendada ou outra janela). Aguarde terminar."
    for _ in range(2):
        try:
            fd = os.open(ARQ, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if ocupado():
                raise JaRodando(msg)
            try:
                ARQ.unlink()  # lock abandonado (processo morreu ou passou de 4h)
            except OSError:
                pass
    else:
        raise JaRodando(msg)
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {time.time()}")
    try:
        yield
    finally:
        try:
            ARQ.unlink()
        except OSError:
            pass
