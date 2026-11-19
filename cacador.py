"""Linha de comando do raspador (usada pelo agendador do Windows).

  python cacador.py                         # todas as fontes ativas, todos os termos
  python cacador.py --fonte linkedin        # so uma fonte
  python cacador.py --termo rpa --termo n8n # so esses termos
  python cacador.py --dias 7                # so vagas dos ultimos 7 dias
  python cacador.py --manter-aberto         # nao fecha o Edge do Glassdoor ao terminar
  python cacador.py --reavaliar             # recalcula o match das vagas salvas apos mudar o config.json

O painel (painel.bat) usa o mesmo codigo.
"""
import argparse
import sys

from raspador import runner
from raspador.trava import JaRodando


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonte", action="append", help="glassdoor | linkedin | gupy | programathor (repetivel)")
    ap.add_argument("--termo", action="append")
    ap.add_argument("--dias", type=int)
    ap.add_argument("--manter-aberto", action="store_true")
    ap.add_argument("--reavaliar", action="store_true", help="recalcula o match das vagas salvas (sem buscar nada)")
    a = ap.parse_args()
    if a.reavaliar:
        print(f"{runner.reavaliar()} vagas reavaliadas.")
        return
    try:
        novas = runner.executar(a.fonte, a.termo, a.dias, manter_aberto=a.manter_aberto)
    except JaRodando as e:
        print(e)
        sys.exit(3)
    rel = runner.relatorio_html(novas)
    if rel:
        print("Relatorio:", rel)
        runner.abrir_arquivo(rel)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
