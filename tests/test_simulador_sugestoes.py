"""Simulador "e se eu aprendesse X?" e sugestoes de lacunas pelos descartes (offline; nunca escreve no config real)."""
import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from fastapi import HTTPException  # noqa: E402

from raspador import config, runner, servidor, simulador, sugestoes  # noqa: E402
from test_correcoes import BancoTemp, bloquear_navegador, liberar_navegador  # noqa: E402

_CONFIG_ANTES = None


def setUpModule():
    global _CONFIG_ANTES
    _CONFIG_ANTES = config.CFG_PATH.read_bytes()      # os testes jamais podem mexer no config.json de verdade
    bloquear_navegador()


def tearDownModule():
    liberar_navegador()
    assert config.CFG_PATH.read_bytes() == _CONFIG_ANTES, "um teste alterou o config.json real!"


def linha(vid, titulo, desc, empresa="Acme"):
    return {"id": vid, "titulo": titulo, "empresa": empresa, "fonte": "gupy", "link": "x", "descricao": desc, "match": 0}


def com_status(status_descricoes):
    return [{"status": s, "descricao": d} for s, d in status_descricoes]


class TestSimulador(unittest.TestCase):
    def setUp(self):
        self.cfg = config.carregar()
        self.linhas = [linha("a", "RPA Developer UiPath", "Requisitos:\nPython e UiPath."),    # UiPath no titulo: -20 hoje
                       linha("b", "RPA Developer", "Requisitos:\nPython.", "Outra")]

    def test_aprender_remove_o_desconto_e_sobe_so_quem_cita(self):
        r = simulador.simular(self.cfg, self.linhas, ["UiPath"])
        por = {i["id"]: i for i in r["melhores"]}
        self.assertGreaterEqual(por["a"]["delta"], 15)
        self.assertEqual(por["b"]["delta"], 0)                    # nao cita UiPath: nada muda
        self.assertEqual((r["subiram"], r["total"]), (1, 2))
        self.assertGreaterEqual(r["ganho_medio"], 15)
        for c in r["cortes"]:
            self.assertGreaterEqual(c["depois"], c["antes"])       # aprender nunca piora

    def test_posicao_no_ranking_melhora_para_quem_sobe(self):
        r = simulador.simular(self.cfg, self.linhas, ["UiPath"])
        a = next(i for i in r["melhores"] if i["id"] == "a")
        self.assertLessEqual(a["pos_depois"], a["pos_antes"])

    def test_tecnologia_que_voce_ja_tem_ou_desconhecida(self):
        with self.assertRaises(ValueError) as cm:
            simulador.simular(self.cfg, self.linhas, ["Python"])
        self.assertIn("já tem", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            simulador.simular(self.cfg, self.linhas, ["Xyzzy"])
        self.assertIn("desconhecida", str(cm.exception))

    def test_varias_tecnologias_de_uma_vez(self):
        r = simulador.simular(self.cfg, self.linhas, ["UiPath", "Power Automate", "UiPath"])
        self.assertEqual(r["tecnologias"], ["UiPath", "Power Automate"])   # sem repetir

    def test_nao_altera_o_config_nem_grava_nada(self):
        antes = copy.deepcopy(self.cfg)
        simulador.simular(self.cfg, self.linhas, ["UiPath"])
        self.assertEqual(self.cfg, antes)

    def test_peso_da_habilidade_e_configuravel(self):
        leve = {**self.cfg, "simulador": {"peso_habilidade": 1, "cortes": [40]}}
        forte = {**self.cfg, "simulador": {"peso_habilidade": 6, "cortes": [40]}}
        g1 = simulador.simular(leve, self.linhas, ["UiPath"])["ganho_medio"]
        g2 = simulador.simular(forte, self.linhas, ["UiPath"])["ganho_medio"]
        self.assertLessEqual(g1, g2)

    def test_opcoes_so_listam_o_que_aparece_nas_vagas(self):
        nomes = {o["tech"] for o in simulador.opcoes(self.cfg, self.linhas)}
        self.assertIn("UiPath", nomes)
        self.assertNotIn("Azure", nomes)                             # nenhuma vaga cita
        self.assertNotIn("Python", nomes)                            # e sua habilidade


class TestSugestoes(unittest.TestCase):
    def setUp(self):
        self.cfg = config.carregar()

    def dados(self, oracle_desc=3, desc=5, pos=2, oracle_pos=0):
        d = ([("descartado", "Requisitos:\nOracle e Python.")] * oracle_desc + [("descartado", "Requisitos:\nPython.")] * (desc - oracle_desc))
        p = ([("interessado", "Requisitos:\nOracle.")] * oracle_pos + [("candidatado", "Requisitos:\nPython.")] * (pos - oracle_pos))
        return com_status(d + p)

    def test_sugere_o_que_aparece_nas_descartadas(self):
        r = sugestoes.sugerir(self.cfg, self.dados())
        self.assertTrue(r["suficiente"])
        o = next(i for i in r["sugestoes"] if i["tech"] == "Oracle")
        self.assertEqual((o["descartadas"], o["pct_descartadas"], o["positivas"], o["pen_sugerida"]), (3, 60, 0, 8))
        self.assertNotIn("Python", {i["tech"] for i in r["sugestoes"]})   # habilidade sua nunca vira lacuna

    def test_nao_sugere_o_que_aparece_tambem_nas_que_voce_curte(self):
        r = sugestoes.sugerir(self.cfg, self.dados(oracle_pos=2))
        self.assertNotIn("Oracle", {i["tech"] for i in r["sugestoes"]})

    def test_poucos_dados_nao_sugere_nada(self):
        r = sugestoes.sugerir(self.cfg, self.dados(oracle_desc=2, desc=2))
        self.assertFalse(r["suficiente"])
        self.assertEqual(r["sugestoes"], [])

    def test_nao_sugere_o_que_ja_desconta_nem_o_ignorado(self):
        rows = com_status([("descartado", "Requisitos:\nGCP e Oracle.")] * 4 + [("candidatado", "Requisitos:\nPython.")])
        nomes = {i["tech"] for i in sugestoes.sugerir(self.cfg, rows)["sugestoes"]}
        self.assertIn("Oracle", nomes)
        self.assertNotIn("GCP / Google Cloud", nomes)                      # GCP ja esta nas lacunas
        ign = {**self.cfg, "lacunas_ignoradas": ["Oracle"]}
        self.assertNotIn("Oracle", {i["tech"] for i in sugestoes.sugerir(ign, rows)["sugestoes"]})

    def test_beneficios_nao_contam(self):
        rows = com_status([("descartado", "Requisitos:\nPython.\nBeneficios:\nOracle University.")] * 4)
        self.assertNotIn("Oracle", {i["tech"] for i in sugestoes.sugerir(self.cfg, rows)["sugestoes"]})

    def test_aponta_descontos_que_o_seu_comportamento_contradiz(self):
        # UiPath ja desconta (ferramenta fraca), mas aparece em 3 das 4 vagas que voce curtiu e em 2 das 5 que descartou
        rows = com_status([("candidatado", "Requisitos:\nUiPath.")] * 3 + [("candidatado", "Requisitos:\nPython.")]
                          + [("descartado", "Requisitos:\nUiPath.")] * 2 + [("descartado", "Requisitos:\nPython.")] * 3)
        rev = {i["tech"]: i for i in sugestoes.sugerir(self.cfg, rows)["revisar"]}
        self.assertEqual((rev["UiPath"]["positivas"], rev["UiPath"]["pct_positivas"], rev["UiPath"]["descartadas"]), (3, 75, 2))
        so_uma = com_status([("candidatado", "Requisitos:\nUiPath.")] + [("descartado", "Requisitos:\nPython.")] * 3)
        self.assertEqual(sugestoes.sugerir(self.cfg, so_uma)["revisar"], [])           # 1 vaga curtida nao prova nada

    def test_aplicar_grava_um_termo_com_a_penalidade_limitada(self):
        novo, termo, pen = sugestoes.aplicar(self.cfg, "Oracle", 8, self.dados())
        self.assertEqual((termo, pen, novo["lacunas"]["oracle"]), ("oracle", 8, 8))
        self.assertNotIn("oracle", self.cfg.get("lacunas", {}))                         # o original nao foi alterado
        self.assertEqual(sugestoes.aplicar(self.cfg, "Oracle", 99, [])[2], 30)           # teto
        self.assertEqual(sugestoes.aplicar(self.cfg, "Oracle", -5, [])[2], 1)            # piso

    def test_aplicar_escolhe_a_grafia_mais_citada_e_limpa_o_ignorado(self):
        rows = com_status([("descartado", "Requisitos:\nNodeJS.")] * 3)
        cfg = {**self.cfg, "lacunas_ignoradas": ["Node.js"]}
        novo, termo, _ = sugestoes.aplicar(cfg, "Node.js", 6, rows)
        self.assertEqual(termo, "nodejs")                                                # "node" nao aparece; "nodejs" sim
        self.assertNotIn("Node.js", novo["lacunas_ignoradas"])
        self.assertEqual(sum(1 for k in novo["lacunas"] if k in ("node", "nodejs")), 1)  # um termo so: sem somar descontos

    def test_aplicar_e_ignorar_validam_a_tecnologia(self):
        with self.assertRaises(ValueError):
            sugestoes.aplicar(self.cfg, "Python", 8, [])         # ja e sua
        with self.assertRaises(ValueError):
            sugestoes.ignorar(self.cfg, "Xyzzy")
        ign = sugestoes.ignorar(sugestoes.ignorar(self.cfg, "Oracle"), "Oracle")
        self.assertEqual(ign["lacunas_ignoradas"].count("Oracle"), 1)                   # idempotente


class TestEndpoints(BancoTemp):
    def popular(self):
        for i in range(5):
            self.add(f"d{i}", titulo=f"RPA Dev {i}", empresa=f"E{i}", descricao="Requisitos:\nPython e Oracle." if i < 3 else "Requisitos:\nPython.")
            self.con.execute("UPDATE vagas SET status='descartado' WHERE id=?", (f"d{i}",))
        for i in range(2):
            self.add(f"p{i}", titulo=f"RPA Pleno {i}", empresa=f"P{i}", descricao="Requisitos:\nPython.")
            self.con.execute("UPDATE vagas SET status='candidatado' WHERE id=?", (f"p{i}",))
        self.con.commit()

    def test_simulador_e_opcoes(self):
        self.popular()
        self.assertIn("Oracle", {o["tech"] for o in servidor.simulador_opcoes()["opcoes"]})
        r = servidor.simular(servidor.SimularIn(tecnologias=["Oracle"]))
        self.assertEqual(r["total"], 7)
        with self.assertRaises(HTTPException) as cm:
            servidor.simular(servidor.SimularIn(tecnologias=["Xyzzy"]))
        self.assertEqual(cm.exception.status_code, 400)

    def test_adicionar_lacuna_grava_e_reavalia(self):
        self.popular()
        with mock.patch.object(config, "salvar") as salvar, mock.patch.object(runner, "reavaliar", return_value=7):
            r = servidor.adicionar_lacuna(servidor.LacunaIn(tech="Oracle", pen=8))
        self.assertEqual((r["termo"], r["pen"], r["reavaliadas"]), ("oracle", 8, 7))
        salvar.assert_called_once()
        self.assertEqual(salvar.call_args[0][0]["lacunas"]["oracle"], 8)

    def test_adicionar_lacuna_durante_uma_busca_nao_reavalia(self):
        self.popular()
        antes = runner.ESTADO["rodando"]
        runner.ESTADO["rodando"] = True
        try:
            with mock.patch.object(config, "salvar"), mock.patch.object(runner, "reavaliar") as reav:
                r = servidor.adicionar_lacuna(servidor.LacunaIn(tech="Oracle", pen=8))
        finally:
            runner.ESTADO["rodando"] = antes
        self.assertIsNone(r["reavaliadas"])
        reav.assert_not_called()

    def test_ignorar_e_tecnologia_invalida(self):
        with mock.patch.object(config, "salvar") as salvar:
            servidor.ignorar_lacuna(servidor.IgnorarIn(tech="Oracle"))
        self.assertIn("Oracle", salvar.call_args[0][0]["lacunas_ignoradas"])
        with mock.patch.object(config, "salvar") as salvar, self.assertRaises(HTTPException) as cm:
            servidor.adicionar_lacuna(servidor.LacunaIn(tech="Python", pen=8))
        self.assertEqual(cm.exception.status_code, 400)
        salvar.assert_not_called()

    def test_sugestoes_via_endpoint(self):
        self.popular()
        r = servidor.sugestoes_lacunas()
        self.assertEqual((r["descartadas"], r["positivas"]), (5, 2))
        self.assertIn("Oracle", {i["tech"] for i in r["sugestoes"]})


class TestConfigETela(unittest.TestCase):
    def test_padroes_no_config(self):
        cfg = config.carregar()
        self.assertEqual(cfg["simulador"]["peso_habilidade"], 3)
        self.assertEqual(cfg["sugestoes"]["penalidade"], 8)
        self.assertIsInstance(cfg["lacunas_ignoradas"], list)

    def test_tela_monta_os_cartoes_sem_innerhtml_e_sem_onclick_inline(self):
        html = (RAIZ / "raspador" / "web" / "index.html").read_text(encoding="utf-8")
        for id_ in ("statSimulador", "statSugestoes"):
            self.assertIn(f'id="{id_}"', html)
        corpo = html[html.index("async function carregaSimulador"):html.index("function montaDescricaoColorida")]
        self.assertNotIn("innerHTML", corpo)                  # titulos e empresas de terceiros so via textContent
        self.assertNotIn('onclick="', corpo)                  # atributo HTML inline com dado do template (atribuicao em JS e ok)


if __name__ == "__main__":
    unittest.main()
