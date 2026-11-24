"""Testes de regressao (offline: nenhuma requisicao real, banco temporario).

Rodar:  python -m unittest discover -s tests -v
"""
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from raspador import analytics, backup, banco, config, dedup, runner, trava, verificador  # noqa: E402
from raspador.fontes import Bloqueio  # noqa: E402
from raspador.fontes.linkedin import LinkedIn  # noqa: E402
from raspador.fontes.programathor import Programathor  # noqa: E402
from raspador.pontuacao import explicar, extrair_metadados  # noqa: E402
from raspador import navegador  # noqa: E402


def cfg_http() -> dict:
    """Config com o LinkedIn em modo HTTP (os testes simulam respostas; nunca abrem o navegador)."""
    cfg = config.carregar()
    cfg["fontes"]["linkedin"]["modo"] = "http"
    return cfg


_abrir_original = navegador.abrir


def _proibido(*a, **k):
    raise AssertionError("os testes nao podem abrir o navegador real (use modo http ou uma pagina falsa)")


def bloquear_navegador():
    navegador.abrir = _proibido


def liberar_navegador():
    navegador.abrir = _abrir_original


def setUpModule():
    bloquear_navegador()


def tearDownModule():
    liberar_navegador()


class Resp:
    def __init__(self, status=200, text="", headers=None, url="https://x.test/"):
        self.status_code, self.text, self.headers, self.url = status, text, headers or {}, url

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SessaoFalsa:
    """Substitui requests.Session: devolve respostas em fila (ou a funcao `resp`) e registra os acessos."""

    def __init__(self, resp):
        self.resp, self.urls, self.headers = resp, [], {}

    def get(self, url, **kw):
        self.urls.append(url)
        return self.resp(url, len(self.urls)) if callable(self.resp) else self.resp

    def close(self):
        pass


class BancoTemp(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._db = banco.DB_PATH
        self._trava = trava.ARQ
        banco.DB_PATH = Path(self._dir.name) / "t.db"
        trava.ARQ = Path(self._dir.name) / "t.lock"
        self.con = banco.conectar()

    def tearDown(self):
        self.con.close()
        banco.DB_PATH, trava.ARQ = self._db, self._trava
        self._dir.cleanup()

    def add(self, vid, titulo="RPA Developer", empresa="Acme", fonte="gupy", score=20, match=50, link=None, **kw):
        banco.inserir(self.con, {"id": vid, "fonte": fonte, "titulo": titulo, "empresa": empresa, "score": score,
                                 "match": match, "link": link if link is not None else f"https://{fonte}.test/{vid}",
                                 "descricao": kw.pop("descricao", "texto"), **kw})


# --------------------------------------------------------------------------- verificador
class TestVerificador(BancoTemp):
    def test_frase_no_rodape_nao_marca_encerrada(self):
        html = "<title>RPA Dev</title><h1>Vaga</h1><footer>Outras vagas: no longer available</footer>"
        self.assertEqual(verificador.avaliar(SessaoFalsa(Resp(200, html)), "https://x.test/1"), "ativa")

    def test_titulo_de_vaga_encerrada(self):
        html = "<title>Vaga encerrada | Gupy</title>"
        self.assertEqual(verificador.avaliar(SessaoFalsa(Resp(200, html)), "https://x.test/1"), "encerrada")

    def test_status(self):
        self.assertEqual(verificador.avaliar(SessaoFalsa(Resp(404)), "u"), "encerrada")
        self.assertEqual(verificador.avaliar(SessaoFalsa(Resp(410)), "u"), "encerrada")
        self.assertEqual(verificador.avaliar(SessaoFalsa(Resp(403)), "u"), "desconhecida")  # bloqueio nao e vaga fechada
        with self.assertRaises(verificador.LimiteAtingido):
            verificador.avaliar(SessaoFalsa(Resp(429)), "u")

    def test_so_fontes_permitidas_e_respeita_pausa(self):
        for f in ("glassdoor", "linkedin", "gupy", "programathor"):
            self.add(f"{f}:1", fonte=f)
        sessao = SessaoFalsa(Resp(200, "<title>ok</title>"))
        with mock.patch.object(verificador.requests, "Session", return_value=sessao), mock.patch("time.sleep") as dorme:
            res = verificador.verificar_ativas(self.con)
        hosts = {u.split("//")[1].split(".")[0] for u in sessao.urls}
        self.assertEqual(hosts, {"gupy", "programathor"})  # Glassdoor e LinkedIn nunca sao consultados
        self.assertEqual(res["verificadas"], 2)
        self.assertGreaterEqual(dorme.call_count, 2)       # espera entre as requisicoes

        banco.pausar(self.con, "programathor", "teste")
        sessao2 = SessaoFalsa(Resp(200, "<title>ok</title>"))
        with mock.patch.object(verificador.requests, "Session", return_value=sessao2), mock.patch("time.sleep"):
            res = verificador.verificar_ativas(self.con)
        self.assertIn("programathor", res["puladas"])
        self.assertTrue(all("programathor" not in u for u in sessao2.urls))

    def test_para_no_429_e_poe_a_fonte_em_pausa(self):
        for i in range(3):
            self.add(f"gupy:{i}", empresa=f"E{i}")
        sessao = SessaoFalsa(lambda url, n: Resp(200, "<title>ok</title>") if n == 1 else Resp(429))
        with mock.patch.object(verificador.requests, "Session", return_value=sessao), mock.patch("time.sleep"):
            res = verificador.verificar_ativas(self.con)
        self.assertTrue(res["interrompida"])
        self.assertEqual(len(sessao.urls), 2)  # parou no 429, nao seguiu para a 3a
        self.assertIsNotNone(banco.pausa_ativa(self.con, "gupy"))


# --------------------------------------------------------------------------- dedup
class TestDedup(unittest.TestCase):
    def test_nivel_entre_parenteses_diferencia(self):
        self.assertNotEqual(dedup.chave_vaga("RPA Developer (Junior)", "Acme"), dedup.chave_vaga("RPA Developer (Senior)", "Acme"))

    def test_ruido_nao_diferencia(self):
        base = dedup.chave_vaga("RPA Developer", "Acme Tecnologia")
        self.assertEqual(base, dedup.chave_vaga("[Remoto] RPA Developer NOVA", "Acme Tecnologia Ltda"))
        self.assertEqual(base, dedup.chave_vaga("RPA Developer - PJ", "Acme"))



class TestDedupBanco(BancoTemp):
    def test_marca_so_a_repetida_e_mantem_niveis_distintos(self):
        self.add("a", titulo="RPA Developer", empresa="Acme", fonte="glassdoor")
        self.add("b", titulo="RPA Developer (Remoto)", empresa="Acme", fonte="linkedin")   # "(Remoto)" e ruido: mesma vaga
        self.add("c", titulo="RPA Developer (Senior)", empresa="Acme", fonte="gupy")       # nivel diferente: vaga diferente
        self.assertEqual(dedup.atualizar_duplicadas(self.con), 1)
        d = {r["id"]: r["duplicada_de"] for r in self.con.execute("SELECT id, duplicada_de FROM vagas")}
        self.assertEqual(d, {"a": "", "b": "a", "c": ""})
        self.assertEqual(len(dedup.carregar_chaves(self.con)), 2)


# --------------------------------------------------------------------------- contrato / metadados
class TestMetadados(unittest.TestCase):
    def test_contrato_vindo_da_fonte(self):
        self.assertEqual(extrair_metadados("Dev", "x", "PJ")["contrato"], "PJ")
        self.assertEqual(extrair_metadados("Dev", "x", "CLT")["contrato"], "CLT")
        self.assertEqual(extrair_metadados("Dev", "x", "CLT / PJ")["contrato"], "CLT / PJ")
        self.assertEqual(extrair_metadados("Dev", "x", "vacancy_legal_entity")["contrato"], "PJ")
        self.assertEqual(extrair_metadados("Dev", "x", "vacancy_type_effective")["contrato"], "CLT")

    def test_salario(self):
        self.assertEqual(extrair_metadados("Dev", "Salario: R$ 6.000 a R$ 8.000", "")["salario"], "R$ 6.000 a R$ 8.000")


class TestReavaliar(BancoTemp):
    def test_nao_apaga_contrato_da_api(self):
        # contrato veio da API (tipo_raw guardado): reavaliar deve reproduzi-lo
        self.add("gupy:1", titulo="RPA Developer", descricao="Atuar com RPA em Python.", tipo_raw="vacancy_legal_entity")
        # linha antiga (antes de existir tipo_raw): o contrato ja gravado nao pode virar vazio
        self.add("gupy:2", titulo="RPA Developer Senior", empresa="Outra", descricao="Atuar com RPA em Python.", contrato="PJ")
        runner.reavaliar()
        c = {r["id"]: r["contrato"] for r in self.con.execute("SELECT id, contrato FROM vagas")}
        self.assertEqual(c["gupy:1"], "PJ")
        self.assertEqual(c["gupy:2"], "PJ")


# --------------------------------------------------------------------------- analytics
class TestAnalytics(BancoTemp):
    def test_so_conta_vagas_relevantes_abertas_e_unicas(self):
        self.add("a", titulo="RPA Developer", empresa="A", descricao="python selenium")
        self.add("b", titulo="Product Manager", empresa="B", score=0, descricao="python")       # irrelevante
        self.add("c", titulo="RPA Dev 2", empresa="C", descricao="python")
        self.con.execute("UPDATE vagas SET duplicada_de='a' WHERE id='c'")                        # duplicada
        self.add("d", titulo="RPA Dev 3", empresa="D", descricao="python")
        self.con.execute("UPDATE vagas SET encerrada=1 WHERE id='d'")                             # encerrada
        self.add("e", titulo="RPA Dev 4", empresa="E", descricao="python")
        self.con.execute("UPDATE vagas SET excluida=1 WHERE id='e'")                              # excluida
        self.con.commit()
        r = analytics.obter_analytics(self.con, 10)
        self.assertEqual(r["total_vagas"], 1)
        self.assertEqual(r["top_tecnologias"][0]["count"], 1)


# --------------------------------------------------------------------------- LinkedIn / Programathor
class TestLinkedIn(unittest.TestCase):
    def test_429_para_na_hora_sem_retry_e_sem_cabecalhos_falsos(self):
        cfg = cfg_http()
        with LinkedIn(cfg, print, threading.Event()) as li:
            sessao = SessaoFalsa(Resp(429, headers={"Retry-After": "120"}))
            li.s = sessao
            with mock.patch("time.sleep") as dorme:
                with self.assertRaises(Bloqueio) as cm:
                    li.buscar("rpa")
            self.assertEqual(len(sessao.urls), 1)      # exatamente 1 acesso: nada de insistir
            self.assertEqual(dorme.call_count, 0)
            self.assertEqual(cm.exception.retry_after, 120)
        enviados = {k.lower() for k in __import__("raspador.fontes.linkedin", fromlist=["HEADERS"]).HEADERS}
        self.assertFalse(any(k.startswith("sec-") for k in enviados))


CARD = ('<div class="cell-list "><a href="/jobs/{id}-dev"><span class="btn-pjax"></span>'
        '<img alt="Acme"><h3>{titulo}</h3><span>{local}</span><span>Sênior</span><span>{tipo}</span></a></div>')


class TestProgramathor(unittest.TestCase):
    def _fonte(self, resp):
        cfg = config.carregar()
        f = Programathor(cfg, print, threading.Event())
        f.s = SessaoFalsa(resp)
        return f

    def test_contrato_vem_do_texto_visivel_e_nao_de_classes_do_html(self):
        c = Programathor._card(CARD.format(id=1, titulo="Dev Python NOVA", local="Remoto", tipo="CLT"))
        self.assertEqual(c["tipo_raw"], "CLT")           # a classe "btn-pjax" nao pode virar PJ
        self.assertEqual(c["titulo"], "Dev Python")      # selo NOVA removido do titulo
        self.assertEqual(c["idade_dias"], 0)             # NOVA = recente
        self.assertIsNone(Programathor._card(CARD.format(id=2, titulo="Dev", local="Presencial", tipo="PJ")))
        c2 = Programathor._card(CARD.format(id=3, titulo="Dev Go", local="Remoto", tipo="PJ"))
        self.assertEqual(c2["tipo_raw"], "PJ")
        self.assertIsNone(c2["idade_dias"])              # sem selo: o site nao informa a data

    def test_busca_usa_pagina_da_tecnologia(self):
        f = self._fonte(Resp(200, CARD.format(id=7, titulo="Dev Python", local="Remoto", tipo="PJ")))
        with mock.patch("time.sleep"):
            vagas = f.buscar("Python")
        self.assertIn("/jobs-python", f.s.urls[0])
        self.assertEqual([v["id"] for v in vagas], ["prog:7"])

    def test_termo_sem_pagina_404_nao_quebra(self):
        f = self._fonte(Resp(404))
        self.assertEqual(f.buscar("rpa"), [])

    def test_429_sem_retry(self):
        f = self._fonte(Resp(429))
        with mock.patch("time.sleep") as dorme:
            with self.assertRaises(Bloqueio):
                f.buscar("python")
        self.assertEqual(len(f.s.urls), 1)
        self.assertEqual(dorme.call_count, 0)


# --------------------------------------------------------------------------- pausa
class TestPausa(BancoTemp):
    def test_retomar_manual_mantem_o_backoff(self):
        banco.pausar(self.con, "linkedin", "429")
        banco.pausar(self.con, "linkedin", "429")                  # 2 falhas: 6h
        banco.encerrar_pausa(self.con, "linkedin")
        self.assertIsNone(banco.pausa_ativa(self.con, "linkedin"))
        self.assertEqual(self.con.execute("SELECT falhas FROM pausas").fetchone()[0], 2)  # contador preservado
        ate = banco.pausar(self.con, "linkedin", "429")            # 3a falha: 12h, nao recomeca em 3h
        self.assertEqual(self.con.execute("SELECT falhas FROM pausas").fetchone()[0], 3)
        self.assertGreater(ate, banco.agora())


# --------------------------------------------------------------------------- runner: dedup antes de baixar
class FonteFalsa:
    NOME, ROTULO = "linkedin", "LinkedIn"
    descricoes = 0
    empresa = "Acme"

    def __init__(self, cfg, log, parar, **_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def buscar(self, termo):
        return [{"id": "li:900", "titulo": "RPA Developer", "empresa": type(self).empresa, "idade_dias": 1, "local": "", "link": "x"}]

    def descricao(self, v):
        type(self).descricoes += 1
        return "Desenvolver robos RPA em Python e Selenium."


class TestRunnerDedup(BancoTemp):
    def _rodar(self):
        FonteFalsa.descricoes = 0
        with mock.patch.dict(runner.FONTES, {"linkedin": FonteFalsa}), mock.patch("time.sleep"):
            runner.executar(["linkedin"], ["rpa"])

    def test_duplicada_de_outra_fonte_nao_baixa_descricao(self):
        self.add("gupy:1", titulo="RPA Developer", empresa="Acme", fonte="gupy")
        FonteFalsa.empresa = "Acme"
        self._rodar()
        self.assertEqual(FonteFalsa.descricoes, 0)
        r = self.con.execute("SELECT duplicada_de FROM vagas WHERE id='li:900'").fetchone()
        self.assertEqual(r["duplicada_de"], "gupy:1")

    def test_vaga_nova_baixa_descricao(self):
        self.add("gupy:1", titulo="RPA Developer", empresa="Outra", fonte="gupy")
        FonteFalsa.empresa = "Acme"
        self._rodar()
        self.assertEqual(FonteFalsa.descricoes, 1)


# --------------------------------------------------------------------------- match: pouca evidencia
class TestCobertura(unittest.TestCase):
    def setUp(self):
        self.cfg = config.carregar()

    def test_pouca_evidencia_nao_chega_a_100(self):
        # so cita APIs REST e webhooks e o titulo diz "Automacao" (caso real: consultor Zendesk dava 100%)
        ex = explicar(self.cfg, "Consultor Zendesk CX e Automação Pleno", "Experiência com Zendesk Suite. Integrações via APIs REST e webhooks.")
        self.assertLess(ex["match"], 60)
        self.assertEqual(ex["titulo"], "fraco")

    def test_automacao_com_sinal_de_rpa_continua_foco(self):
        ex = explicar(self.cfg, "Analista de Automação Pleno", "Atuar com RPA, Python e Selenium.")
        self.assertEqual(ex["titulo"], "foco")

    def test_muita_evidencia_continua_alta(self):
        ex = explicar(self.cfg, "RPA Developer", "Requisitos: Python, Selenium, Playwright, RPA, APIs REST, FastAPI e Docker.")
        self.assertGreaterEqual(ex["match"], 75)

    def test_suavizacao_e_configuravel(self):
        d = "Integrações via APIs REST e webhooks."
        sem = explicar({**self.cfg, "cobertura_prior": 0}, "RPA Developer", d)["cobertura"]
        com = explicar({**self.cfg, "cobertura_prior": 3}, "RPA Developer", d)["cobertura"]
        self.assertEqual(sem, 100)   # sem suavizacao, 3 skills genericas = 100% (o defeito)
        self.assertLess(com, 70)


# --------------------------------------------------------------------------- backup do banco
class TestBackup(BancoTemp):
    def test_backup_fica_ao_lado_do_banco_e_nunca_na_pasta_real(self):
        self.add("a")
        p = backup.fazer_backup()
        self.assertEqual(p.parent, Path(self._dir.name) / "backups")
        self.assertNotEqual(backup.pasta(), config.BASE / "backups")

    def test_cria_copia_valida_e_nao_duplica_no_mesmo_dia(self):
        self.add("a")
        self.add("b", empresa="B")
        p = backup.fazer_backup()
        self.assertTrue(p.exists())
        con = sqlite3.connect(p)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM vagas").fetchone()[0], 2)
        finally:
            con.close()
        self.assertIsNone(backup.fazer_backup())                # ja existe o de hoje
        self.assertIsNotNone(backup.fazer_backup(forcar=True))  # manual: sempre cria
        self.assertEqual(len(backup.listar()), 2)

    def test_rodizio_mantem_so_os_mais_recentes(self):
        self.add("a")
        backup.pasta().mkdir(parents=True)
        for d in range(1, 11):
            (backup.pasta() / f"vagas_202601{d:02d}.db").write_bytes(b"x")
        backup.fazer_backup(manter=7)
        nomes = [a["arquivo"] for a in backup.listar()]
        self.assertEqual(len(nomes), 7)
        self.assertIn(f"vagas_{datetime.now():%Y%m%d}.db", nomes)  # o de hoje sobrevive
        self.assertNotIn("vagas_20260101.db", nomes)               # o mais antigo saiu

    def test_sem_banco_nao_quebra(self):
        banco.DB_PATH = Path(self._dir.name) / "nao_existe.db"
        self.assertIsNone(backup.fazer_backup())
        self.assertIsNone(backup.garantir_backup_diario())

    def test_garantir_backup_nunca_levanta(self):
        logs = []
        with mock.patch.object(backup, "fazer_backup", side_effect=RuntimeError("disco cheio")):
            self.assertIsNone(backup.garantir_backup_diario(logs.append))
        self.assertTrue(any("disco cheio" in m for m in logs))

    def test_copia_invalida_e_descartada(self):
        self.add("a")
        with mock.patch.object(backup, "_contar", return_value=("corrompido", 0)):
            with self.assertRaises(RuntimeError):
                backup.fazer_backup()
        self.assertEqual(list(backup.pasta().glob("*")), [])  # nem a copia nem o .tmp ficam


# --------------------------------------------------------------------------- lacunas do mercado
class TestMercado(BancoTemp):
    def test_lacunas_e_pontos_fortes(self):
        self.add("a", titulo="RPA Developer", empresa="A",
                 descricao="Requisitos:\nPython e Selenium.\nExperiência com GCP.\nBeneficios:\nCurso de Azure.")
        self.add("b", titulo="RPA Dev 2", empresa="B", descricao="Requisitos:\nPython.\nDiferenciais:\nGoogle Cloud e GCP.")
        self.add("c", titulo="RPA Dev 3", empresa="C", descricao="Requisitos:\nPython.")
        r = analytics.obter_analytics(self.con, 10, config.carregar())
        lac = {i["tech"]: i for i in r["lacunas_mercado"]}
        gcp = lac["GCP / Google Cloud"]
        self.assertEqual((gcp["vagas"], gcp["obrigatorio"], gcp["diferencial"]), (2, 1, 1))  # sinonimos: 1x por vaga
        self.assertTrue(gcp["penalizada"])                                                   # GCP ja desconta no match
        self.assertNotIn("Azure", lac)    # so aparece em "Beneficios": nao conta
        self.assertNotIn("Python", lac)   # e ponto forte, nao lacuna
        fortes = {i["tech"]: i for i in r["pontos_fortes"]}
        self.assertEqual(fortes["Python"]["vagas"], 3)
        self.assertEqual(fortes["Python"]["pct"], 100.0)

    def test_sem_vagas_devolve_listas_vazias(self):
        r = analytics.obter_analytics(self.con, 10, config.carregar())
        self.assertEqual((r["lacunas_mercado"], r["pontos_fortes"]), ([], []))

    def test_tela_tem_os_cartoes_e_a_secao_de_backups(self):
        html = (RAIZ / "raspador" / "web" / "index.html").read_text(encoding="utf-8")
        for id_ in ("statLacunas", "statFortes", "btnBackup", "bkLista"):
            self.assertIn(f'id="{id_}"', html)


# --------------------------------------------------------------------------- tela: sem XSS por onclick
class TestTela(unittest.TestCase):
    def test_nenhum_onclick_inline_com_dado_do_template(self):
        html = (RAIZ / "raspador" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'onclick="[^"]*\$\{', html), "onclick inline com ${...}: possivel XSS")
        self.assertIn("data-empresa", html)


if __name__ == "__main__":
    unittest.main()
