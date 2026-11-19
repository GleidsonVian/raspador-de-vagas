"""Fontes de vagas. Cada fonte e um context manager com:
    NOME, ROTULO
    buscar(termo) -> list[dict]   (id, titulo, empresa, nota, idade_dias, local, easy, link)
    descricao(vaga) -> str
Para adicionar um site novo: crie um arquivo aqui com uma classe assim e registre em FONTES.
"""
from .glassdoor import Glassdoor
from .linkedin import LinkedIn

FONTES = {Glassdoor.NOME: Glassdoor, LinkedIn.NOME: LinkedIn}


class Bloqueio(Exception):
    """O site pediu para parar (429/captcha). A fonte e abandonada e entra em pausa (ver banco.pausar)."""

    def __init__(self, msg: str, retry_after: int | None = None):
        super().__init__(msg)
        self.retry_after = retry_after  # segundos, se o site informou (header Retry-After)
