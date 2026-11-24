"""LinkedIn em modo navegador, com uma pagina FALSA no lugar do Edge (testes nunca abrem navegador)."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from raspador import config, navegador  # noqa: E402
from raspador.fontes import Bloqueio  # noqa: E402
from raspador.fontes.linkedin import LinkedIn  # noqa: E402
from test_correcoes import bloquear_navegador, liberar_navegador  # noqa: E402


def setUpModule():
    bloquear_navegador()


def tearDownModule():
    liberar_navegador()


CARDS = ('<ul class="jobs-search__results-list">'
         '<li><div class="base-card base-search-card" data-entity-urn="urn:li:jobPosting:111">'
         '<a href="https://www.linkedin.com/jobs/view/rpa-developer-111?x=1"></a>'
         '<h3 class="base-search-card__title">RPA Developer</h3><h4 class="base-search-card__subtitle">Acme</h4>'
         '<span class="job-search-card__location">Brasil</span><time datetime="2026-11-20">h</time></div></li>'
         '<li class="outra-classe"><div class="base-card" data-entity-urn="urn:li:jobPosting:222">'
         '<h3 class="base-search-card__title">Analista de Automação</h3><h4 class="base-search-card__subtitle">Beta</h4></div></li>'
         '<li><a href="/feed">menu sem vaga</a></li>'
         '<li><a href="/jobs/view/algo-23309196">link solto, sem titulo de card</a></li></ul>')


class PaginaFalsa:
    def __init__(self, html=CARDS, url="https://www.linkedin.com/jobs/search/", titulo="Vagas de rpa", status=200, desc=(200, "")):
        self.url, self._titulo, self._html, self._status, self._desc = url, titulo, html, status, desc
        self.visitadas, self.avaliados = [], []

    def goto(self, url, **kw):
        self.visitadas.append(url)
        return SimpleNamespace(status=self._status)

    def title(self):
        return self._titulo

    def content(self):
        return self._html

    def wait_for_selector(self, *a, **k):
        pass

    def evaluate(self, js, arg):
        self.avaliados.append(arg)
        return list(self._desc)


def linkedin(pagina):
    li = LinkedIn(config.carregar(), print, threading.Event())   # modo padrao do config.json: navegador
    li.page = pagina
    return li


class TestModoNavegador(unittest.TestCase):
    def test_o_padrao_e_o_navegador_real(self):
        self.assertEqual(config.carregar()["fontes"]["linkedin"]["modo"], "navegador")

    def test_os_testes_nao_conseguem_abrir_o_navegador(self):
        with self.assertRaises(AssertionError):
            navegador.abrir()

    def test_le_os_cards_e_usa_a_pagina_publica_de_busca(self):
        p = PaginaFalsa()
        with mock.patch("time.sleep"):
            vagas = linkedin(p).buscar("rpa")
        self.assertEqual([v["id"] for v in vagas], ["li:111", "li:222"])     # o <li> do menu e ignorado
        self.assertEqual((vagas[0]["titulo"], vagas[0]["empresa"]), ("RPA Developer", "Acme"))
        url = p.visitadas[0]
        self.assertIn("/jobs/search/", url)
        self.assertNotIn("jobs-guest", url)                                  # nao usa o endpoint HTTP
        for trecho in ("keywords=rpa", "f_WT=2", "geoId=106057199"):
            self.assertIn(trecho, url)

    def test_pedido_de_login_para_na_hora_e_nunca_entra_na_conta(self):
        p = PaginaFalsa(url="https://www.linkedin.com/authwall?trk=x")
        with mock.patch("time.sleep"):
            with self.assertRaises(Bloqueio) as cm:
                linkedin(p).buscar("rpa")
        self.assertIn("login", str(cm.exception))
        self.assertEqual(len(p.visitadas), 1)                                # nao tentou nada alem da busca

    def test_verificacao_resolvida_pelo_usuario_segue_a_busca(self):
        p = PaginaFalsa(titulo="Just a moment...")

        def usuario_resolve(*a, **k):
            p._titulo = "Vagas de rpa"
            return True
        with mock.patch.object(navegador, "esperar_liberacao", side_effect=usuario_resolve) as esperar, mock.patch("time.sleep"):
            vagas = linkedin(p).buscar("rpa")
        esperar.assert_called_once()
        self.assertEqual(len(vagas), 2)

    def test_verificacao_nao_resolvida_vira_bloqueio(self):
        p = PaginaFalsa(titulo="Just a moment...")
        with mock.patch.object(navegador, "esperar_liberacao", return_value=False), mock.patch("time.sleep"):
            with self.assertRaises(Bloqueio) as cm:
                linkedin(p).buscar("rpa")
        self.assertIn("não foi resolvida", str(cm.exception))

    def test_status_429_na_navegacao_conta_como_verificacao(self):
        p = PaginaFalsa(status=429)
        with mock.patch.object(navegador, "esperar_liberacao", return_value=False) as esperar, mock.patch("time.sleep"):
            with self.assertRaises(Bloqueio):
                linkedin(p).buscar("rpa")
        esperar.assert_called_once()

    def test_descricao_via_fetch_dentro_da_pagina(self):
        p = PaginaFalsa(desc=(200, '<div class="show-more-less-html__markup">Texto <b>da</b> vaga</div>'))
        li = linkedin(p)
        with mock.patch("time.sleep"):
            self.assertEqual(li.descricao({"id": "li:111", "link": "x"}), "Texto da vaga")
        self.assertEqual(p.avaliados, ["111"])                               # id sem o prefixo "li:"
        self.assertEqual(li.baixadas, 1)

    def test_descricao_429_vira_bloqueio_e_404_fica_vazia(self):
        with mock.patch("time.sleep"):
            with self.assertRaises(Bloqueio):
                linkedin(PaginaFalsa(desc=(429, ""))).descricao({"id": "li:1", "link": "x"})
            self.assertEqual(linkedin(PaginaFalsa(desc=(404, ""))).descricao({"id": "li:1", "link": "x"}), "")

    def test_parar_cancela_a_busca_sem_abrir_pagina(self):
        li = linkedin(PaginaFalsa())
        li.parar.set()
        self.assertEqual(li.buscar("rpa"), [])
        self.assertEqual(li.page.visitadas, [])


class TestEsperarLiberacao(unittest.TestCase):
    def test_libera_quando_o_usuario_resolve(self):
        estados = iter([True, True, False])
        with mock.patch("time.sleep"):
            self.assertTrue(navegador.esperar_liberacao(lambda: next(estados), lambda m: None, None, "X", segundos_max=60))

    def test_desiste_se_o_tempo_acaba_ou_se_cancelam(self):
        with mock.patch("time.sleep"):
            self.assertFalse(navegador.esperar_liberacao(lambda: True, lambda m: None, None, "X", segundos_max=0))
            ev = threading.Event()
            ev.set()
            self.assertFalse(navegador.esperar_liberacao(lambda: True, lambda m: None, ev, "X", segundos_max=60))


if __name__ == "__main__":
    unittest.main()
