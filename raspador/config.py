import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CFG_PATH = BASE / "config.json"
DB_PATH = BASE / "vagas.db"
PERFIL_EDGE = BASE / "perfil_edge"


def carregar() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def salvar(cfg: dict) -> None:
    tmp = CFG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CFG_PATH)
