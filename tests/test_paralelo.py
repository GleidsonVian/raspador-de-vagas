"""Faixas em paralelo: fontes HTTP rodam junto com a faixa do navegador; as do navegador rodam em sequencia."""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from raspador import banco, config, runner  # noqa: E402
from raspador.fontes import Bloqueio  # noqa: E402
from test_correcoes import BancoTemp, bloquear_navegador, liberar_navegador  # noqa: E402


def setUpModule():
    bloquear_navegador()


def tearDownModule():
    liberar_navegador()


def dorme(seg):
    """Espera real que NAO e afetada pelo mock de time.sleep do runner."""
    threading.Event().wait(seg)


class Cena:
    """Registra o que as fontes falsas fazem, para os testes conferirem ordem e simultaneidade."""

    def __init__(self, barreira=None, tempo_barreira=10):
        self.eventos, self.trava = [], threading.Lock()
        self.ativos = self.max_ativos = 0
        self.barreira, self.tempo_barreira = barreira, tempo_barreira

    def registrar(self, *e):
        with self.trava:
            self.eventos.append(e)

    def entrar(self):
        with self.trava:
            self.ativos += 1
            self.max_ativos = max(self.max_ativos, self.ativos)

    def sair(self):
        with self.trava:
            self.ativos -= 1


def fonte_falsa(cena, nome, navegador, espera=0.0, falha_ao_abrir=False, bloqueia=False, na_barreira=False):
    class F:
        NOME, ROTULO = nome, nome.capitalize()

        @staticmethod
        def usa_navegador(cfg):
            return navegador

        def __init__(self, cfg, log, parar, **_):
            pass

        def __enter__(self):
            if falha_ao_abrir:
                raise RuntimeError("boom")
            return self

        def __exit__(self, *a):
            pass

        def buscar(self, termo):
            cena.registrar("ini", nome)
            cena.entrar()
            try:
                if na_barreira and cena.barreira:
                    try:
                        cena.barreira.wait(timeout=cena.tempo_barreira)  # so passa se as OUTRAS fontes rodarem juntas
                    except threading.BrokenBarrierError:
                        return []
                if bloqueia:
                    raise Bloqueio("bloqueado de proposito")
                dorme(espera)
                return [{"id": f"{nome}:1", "titulo": f"RPA Developer {nome}", "empresa": f"Empresa {nome}",
                         "idade_dias": 1, "local": "", "link": "x"}]
            finally:
                cena.sair()
                cena.registrar("fim", nome)

        def descricao(self, v):
            return "Desenvolver robos RPA em Python e Selenium."
    return F


NOMES = ("glassdoor", "linkedin", "gupy", "programathor")


class TestFaixas(unittest.TestCase):
    def test_faixas_agrupam_as_fontes_do_navegador(self):
        cfg = config.carregar()
        self.assertEqual(runner._faixas(list(NOMES), cfg), [["glassdoor", "linkedin"], ["gupy"], ["programathor"]])
        http = config.carregar()
        http["fontes"]["linkedin"]["modo"] = "http"          # LinkedIn sem navegador: sai da faixa do Edge
        self.assertEqual(runner._faixas(list(NOMES), http), [["glassdoor"], ["linkedin"], ["gupy"], ["programathor"]])
        seq = config.carregar()
        seq["paralelo"] = False
        self.assertEqual(runner._faixas(list(NOMES), seq), [list(NOMES)])
        self.assertEqual(runner._faixas(["glassdoor", "xyz"], cfg), [["glassdoor"], ["xyz"]])


class TestExecucaoParalela(BancoTemp):
    def _rodar(self, fontes, cfg_extra=None, fontes_nomes=NOMES):
        cfg = config.carregar()
        cfg["pausa_entre_buscas_seg"] = [0, 0]
        cfg.update(cfg_extra or {})
        with mock.patch.dict(runner.FONTES, {f.NOME: f for f in fontes}), \
                mock.patch.object(config, "carregar", return_value=cfg), mock.patch("time.sleep"):
            return runner.executar(list(fontes_nomes), ["x"])

    def test_http_roda_junto_com_o_navegador_de_verdade(self):
        cena = Cena(barreira=threading.Barrier(3))
        fontes = [fonte_falsa(cena, "glassdoor", True, na_barreira=True), fonte_falsa(cena, "linkedin", True),
                  fonte_falsa(cena, "gupy", False, na_barreira=True), fonte_falsa(cena, "programathor", False, na_barreira=True)]
        self._rodar(fontes)
        # Glassdoor, Gupy e Programathor esperaram umas pelas outras: se fosse sequencial a barreira quebraria
        self.assertFalse(cena.barreira.broken)
        linhas = {r["fonte"]: r for r in self.con.execute("SELECT * FROM execucoes")}
        self.assertEqual(set(linhas), set(NOMES))
        self.assertTrue(all(r["lidas"] == 1 and r["erro"] is None for r in linhas.values()))

    def test_a_prova_da_barreira_detecta_execucao_sequencial(self):
        # contraprova: com o paralelismo DESLIGADO a mesma barreira tem que quebrar (senao o teste acima nao provaria nada)
        cena = Cena(barreira=threading.Barrier(3), tempo_barreira=0.4)
        fontes = [fonte_falsa(cena, "glassdoor", True, na_barreira=True), fonte_falsa(cena, "linkedin", True),
                  fonte_falsa(cena, "gupy", False, na_barreira=True), fonte_falsa(cena, "programathor", False, na_barreira=True)]
        self._rodar(fontes, {"paralelo": False})
        self.assertTrue(cena.barreira.broken)

    def test_fontes_do_navegador_nunca_rodam_ao_mesmo_tempo(self):
        cena = Cena()
        fontes = [fonte_falsa(cena, "glassdoor", True, espera=0.2), fonte_falsa(cena, "linkedin", True, espera=0.2)]
        self._rodar(fontes, fontes_nomes=("glassdoor", "linkedin"))
        self.assertEqual(cena.max_ativos, 1)                                              # mesmo Edge: um por vez
        ordem = [e for e in cena.eventos if e[0] in ("ini", "fim")]
        self.assertEqual(ordem, [("ini", "glassdoor"), ("fim", "glassdoor"), ("ini", "linkedin"), ("fim", "linkedin")])

    def test_sem_paralelismo_tudo_roda_em_sequencia(self):
        cena = Cena()
        fontes = [fonte_falsa(cena, n, n in ("glassdoor", "linkedin"), espera=0.1) for n in NOMES]
        self._rodar(fontes, {"paralelo": False})
        self.assertEqual(cena.max_ativos, 1)

    def test_erro_em_uma_faixa_nao_derruba_as_outras(self):
        cena = Cena()
        fontes = [fonte_falsa(cena, "glassdoor", True), fonte_falsa(cena, "linkedin", True),
                  fonte_falsa(cena, "gupy", False, falha_ao_abrir=True), fonte_falsa(cena, "programathor", False)]
        self._rodar(fontes)
        linhas = {r["fonte"]: r for r in self.con.execute("SELECT * FROM execucoes")}
        self.assertTrue(linhas["gupy"]["erro"].startswith("RuntimeError"))
        self.assertTrue(all(linhas[n]["erro"] is None and linhas[n]["lidas"] == 1 for n in ("glassdoor", "linkedin", "programathor")))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM vagas").fetchone()[0], 3)

    def test_bloqueio_poe_em_pausa_so_a_fonte_bloqueada(self):
        cena = Cena()
        fontes = [fonte_falsa(cena, "glassdoor", True), fonte_falsa(cena, "linkedin", True, bloqueia=True),
                  fonte_falsa(cena, "gupy", False), fonte_falsa(cena, "programathor", False)]
        self._rodar(fontes)
        self.assertIsNotNone(banco.pausa_ativa(self.con, "linkedin"))
        for n in ("glassdoor", "gupy", "programathor"):
            self.assertIsNone(banco.pausa_ativa(self.con, n))

    def test_estado_do_painel_fecha_limpo_e_conta_tudo(self):
        cena = Cena()
        self._rodar([fonte_falsa(cena, n, n in ("glassdoor", "linkedin")) for n in NOMES])
        self.assertEqual(runner.ESTADO["ativas"], {})                 # nenhuma fonte "ativa" depois de terminar
        self.assertFalse(runner.ESTADO["rodando"])
        self.assertEqual(runner.ESTADO["feito"], runner.ESTADO["total"])
        self.assertEqual(runner.ESTADO["novas"], 4)                   # uma vaga nova por fonte, sem perder contagem

    def test_sem_database_is_locked_com_escritas_simultaneas(self):
        cena = Cena()
        self._rodar([fonte_falsa(cena, n, n in ("glassdoor", "linkedin")) for n in NOMES])
        ids = {r["id"] for r in self.con.execute("SELECT id FROM vagas")}
        self.assertEqual(ids, {f"{n}:1" for n in NOMES})


if __name__ == "__main__":
    unittest.main()
