"""Testes do diagnostico de bloqueios: mensagem do LinkedIn, aviso de pausa velho e alerta falso da Programathor."""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from raspador import banco, config, runner, servidor  # noqa: E402
from raspador.fontes import Bloqueio  # noqa: E402
from raspador.fontes.linkedin import LinkedIn  # noqa: E402
from raspador.fontes.programathor import Programathor  # noqa: E402
from test_correcoes import BancoTemp, Resp, SessaoFalsa, bloquear_navegador, cfg_http, liberar_navegador  # noqa: E402


def setUpModule():
    bloquear_navegador()


def tearDownModule():
    liberar_navegador()

DESAFIO_CLOUDFLARE = "var _0x26bb58=_0x3a5d;function _0x2511(){var _0x2338e3=['__proto__','http:','location'];}"


class TestMensagemDoBloqueio(unittest.TestCase):
    def _bloqueio(self, resp):
        with LinkedIn(cfg_http(), print, threading.Event()) as li:
            li.s = SessaoFalsa(resp)
            with mock.patch("time.sleep"):
                with self.assertRaises(Bloqueio) as cm:
                    li.buscar("rpa")
        return str(cm.exception)

    def test_desafio_do_cloudflare_e_identificado(self):
        msg = self._bloqueio(Resp(429, DESAFIO_CLOUDFLARE, {"Server": "cloudflare", "Set-Cookie": "__cf_bm=abc; path=/"}))
        self.assertIn("Cloudflare", msg)
        self.assertIn("não é limite de volume", msg)

    def test_429_comum_continua_sendo_limite_de_acesso(self):
        msg = self._bloqueio(Resp(429, "Too Many Requests", {"Server": "nginx"}))
        self.assertIn("limitou o acesso", msg)
        self.assertNotIn("Cloudflare", msg)


class TestAvisoDePausaVelho(BancoTemp):
    def test_msg_erro_remove_o_sufixo_de_pausa(self):
        antigo = "LinkedIn limitou o acesso (HTTP 429) - fonte em pausa ate 12:07"
        novo = servidor._msg_erro(antigo)
        self.assertNotIn("pausa ate", novo)
        self.assertIn("a pausa já terminou", novo)
        self.assertEqual(servidor._msg_erro("falhou: timeout"), "falhou: timeout")  # sem sufixo: intacto

    def test_saude_nao_mostra_pausa_que_ja_expirou(self):
        self.con.execute("INSERT INTO execucoes(fonte,inicio,fim,termos,lidas,novas,relevantes,erro) VALUES(?,?,?,?,?,?,?,?)",
                         ("linkedin", "2026-11-23T09:06:00", "2026-11-23T09:09:00", 4, 10, 0, 0,
                          "LinkedIn limitou o acesso (HTTP 429) - fonte em pausa ate 12:07"))
        self.con.commit()
        s = servidor.saude()["linkedin"]            # nenhuma pausa ativa na tabela: ja expirou
        self.assertEqual(s["estado"], "erro")
        self.assertNotIn("pausa ate", s["mensagem"])

    def test_pausa_ativa_continua_aparecendo(self):
        banco.pausar(self.con, "linkedin", "LinkedIn limitou o acesso (HTTP 429)")
        s = servidor.saude()["linkedin"]
        self.assertEqual(s["estado"], "pausa")
        self.assertIn("em pausa ate", s["mensagem"])


class FonteComUmaVaga:
    """1 vaga no primeiro termo e nada nos outros 4 (como uma pagina de tecnologia sem vagas remotas)."""
    NOME, ROTULO = "linkedin", "LinkedIn"

    def __init__(self, cfg, log, parar, **_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def buscar(self, termo):
        if termo != "a":
            return []
        return [{"id": "li:1", "titulo": "RPA Developer", "empresa": "Acme", "idade_dias": 1, "local": "", "link": "x"}]

    def descricao(self, v):
        return "Desenvolver robos RPA em Python e Selenium."


class TestAlertaDeBuscasVazias(BancoTemp):
    def _rodar(self, cls):
        with mock.patch.dict(runner.FONTES, {"linkedin": cls}), mock.patch("time.sleep"):
            runner.executar(["linkedin"], ["a", "b", "c", "d", "e"])
        return self.con.execute("SELECT alerta FROM execucoes ORDER BY id DESC LIMIT 1").fetchone()["alerta"]

    def test_fonte_comum_alerta_quando_4_de_5_buscas_vem_vazias(self):
        self.assertIn("4 de 5 buscas vieram vazias", self._rodar(FonteComUmaVaga))

    def test_fonte_que_opta_por_nao_alertar_nao_gera_alerta_falso(self):
        class FonteTecnologia(FonteComUmaVaga):
            ALERTAR_BUSCAS_VAZIAS = False
        self.assertIsNone(self._rodar(FonteTecnologia))

    def test_programathor_opta_por_nao_alertar(self):
        self.assertFalse(Programathor.ALERTAR_BUSCAS_VAZIAS)


if __name__ == "__main__":
    unittest.main()
