"""Testes do checklist por vaga (offline)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from fastapi import HTTPException  # noqa: E402

from raspador import checklist, config, servidor  # noqa: E402
from test_correcoes import BancoTemp  # noqa: E402

CFG = config.carregar()
DESC = ("Responsabilidades:\nDesenvolver robos.\nRequisitos:\nPython e Selenium.\nExperiência com GCP.\n"
        "Diferenciais:\nDocker e AWS.\nBeneficios:\nCurso de Azure.")


def vaga(**kw):
    base = {"titulo": "RPA Developer Pleno", "descricao": DESC, "contrato": "PJ", "salario": "R$ 6.000 a R$ 8.000",
            "easy": 1, "link": "https://x.test/v", "idade_dias": 3, "local": "Remoto", "fonte": "gupy"}
    base.update(kw)
    return base


class TestMontar(unittest.TestCase):
    def test_separa_obrigatorio_de_diferencial_e_ignora_beneficios(self):
        c = checklist.montar(CFG, vaga(), {})
        tem = {(i["tech"], i["onde"]) for i in c["voce_tem"]}
        falta = {(i["tech"], i["onde"]): i for i in c["falta"]}
        self.assertLessEqual({("Python", "obrigatorio"), ("Selenium", "obrigatorio"), ("Docker", "diferencial")}, tem)
        gcp = falta[("GCP / Google Cloud", "obrigatorio")]
        self.assertTrue(gcp["descontada"])                       # GCP ja desconta no match
        self.assertIn(("AWS", "diferencial"), falta)
        self.assertNotIn("Azure", {t for t, _ in falta})         # so aparece em "Beneficios": nao conta
        r = c["resumo"]
        self.assertEqual((r["cobertas"], r["obrigatorias_total"], r["pct"]), (2, 3, 67))

    def test_obrigatorios_vem_antes_dos_diferenciais(self):
        c = checklist.montar(CFG, vaga(), {})
        ondes = [i["onde"] for i in c["voce_tem"]]
        self.assertEqual(ondes, sorted(ondes, key=lambda o: 0 if o == "obrigatorio" else 1))

    def test_destaque_so_nas_habilidades_que_tem_frase(self):
        c = checklist.montar(CFG, vaga(), {"python": "Meu projeto em Python"})
        por = {i["tech"]: i["destaque"] for i in c["voce_tem"]}
        self.assertEqual(por["Python"], "Meu projeto em Python")
        self.assertEqual(por["Selenium"], "")

    def test_vaga_sem_descricao(self):
        c = checklist.montar(CFG, vaga(descricao=""), {})
        self.assertFalse(c["tem_descricao"])
        self.assertEqual((c["voce_tem"], c["falta"]), ([], []))
        self.assertTrue(c["info"]["candidatura_rapida"])         # as informacoes basicas continuam

    def test_nivel(self):
        av = lambda t, cfg=CFG: checklist.avaliar_nivel(cfg, t)["avaliacao"]  # noqa: E731
        self.assertEqual(av("Desenvolvedor RPA Sênior"), "acima")
        self.assertEqual(av("Analista RPA Pleno"), "compativel")
        self.assertEqual(av("RPA Developer Júnior"), "abaixo")
        self.assertEqual(av("RPA e Automação Junior, Pleno e Senior"), "compativel")   # faixa que inclui o seu
        self.assertEqual(av("RPA Developer"), "nao_informado")
        self.assertEqual(av("RPA Developer Pleno", {**CFG, "meu_nivel": "senior"}), "abaixo")


class TestDestaquesLocais(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._arq = checklist.ARQ_LOCAL
        checklist.ARQ_LOCAL = Path(self._dir.name) / "perfil_local.json"

    def tearDown(self):
        checklist.ARQ_LOCAL = self._arq
        self._dir.cleanup()

    def test_arquivo_ausente_ou_invalido_nao_quebra(self):
        self.assertEqual(checklist.carregar_destaques(), {})                       # ausente
        checklist.ARQ_LOCAL.write_text("{isso nao e json", encoding="utf-8")
        self.assertEqual(checklist.carregar_destaques(), {})                       # invalido
        checklist.ARQ_LOCAL.write_text('{"destaques": ["lista"]}', encoding="utf-8")
        self.assertEqual(checklist.carregar_destaques(), {})                       # formato errado

    def test_normaliza_chaves_e_descarta_vazios(self):
        checklist.ARQ_LOCAL.write_text('{"destaques": {"  Python ": " texto ", "Selenium": "   "}}', encoding="utf-8")
        self.assertEqual(checklist.carregar_destaques(), {"python": "texto"})


class TestEndpointEPrivacidade(BancoTemp):
    def test_endpoint_devolve_o_checklist_e_404_para_vaga_inexistente(self):
        self.add("gupy:1", titulo="RPA Developer Pleno", descricao=DESC)
        c = servidor.checklist_da_vaga("gupy:1")
        self.assertTrue(c["tem_descricao"])
        self.assertIn("Python", {i["tech"] for i in c["voce_tem"]})
        with self.assertRaises(HTTPException) as cm:
            servidor.checklist_da_vaga("nao:existe")
        self.assertEqual(cm.exception.status_code, 404)

    def test_perfil_local_nunca_vai_para_o_git(self):
        linhas = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("perfil_local.json", [l.strip() for l in linhas])      # frases do curriculo: so locais
        exemplo = json.loads((RAIZ / "perfil_local.example.json").read_text(encoding="utf-8"))
        self.assertTrue(all(f.startswith("Ex.") for f in exemplo["destaques"].values()))  # o modelo publico so tem exemplos

    def test_tela_monta_o_checklist_sem_innerhtml(self):
        html = (RAIZ / "raspador" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="mini chk"', html)
        corpo = html[html.index("function montaChecklist"):html.index("function montaDescricaoColorida")]
        self.assertNotIn("innerHTML", corpo)                                 # textos de terceiros so via textContent


if __name__ == "__main__":
    unittest.main()
