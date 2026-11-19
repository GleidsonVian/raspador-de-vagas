"""Nomes e agrupamento de tecnologias, compartilhado pelas estatisticas e pelo checklist."""

# varias grafias contam como UMA tecnologia (uma vaga que cita "GCP" e "Google Cloud" conta uma vez)
GRUPOS = {
    "GCP / Google Cloud": ("gcp", "google cloud"),
    "Azure": ("azure", "azure devops"),
    "C# / .NET": ("c#", ".net", "dotnet"),
    "Power Platform": ("power platform", "power apps", "dataverse", "pl-500", "pl-400", "pl-200"),
    "REST / APIs": ("rest", "apis rest", "api rest"),
    "PostgreSQL": ("postgres", "postgresql"),
    "Webhooks": ("webhook", "webhooks"),
    "Node.js": ("node", "nodejs"),
}

_ROTULOS = {"uipath": "UiPath", "git": "Git", "javascript": "JavaScript", "typescript": "TypeScript", "n8n": "n8n",
            "pyautogui": "PyAutoGUI", "fastapi": "FastAPI", "mysql": "MySQL", "sqlite": "SQLite", "ocr": "OCR"}


def rotulo(termo: str) -> str:
    if termo in _ROTULOS:
        return _ROTULOS[termo]
    return termo.upper() if len(termo) <= 3 else termo.title()


def agrupar(termos) -> dict:
    """{rotulo: {termos}} juntando sinonimos."""
    inv = {t: rot for rot, ts in GRUPOS.items() for t in ts}
    out: dict = {}
    for t in termos:
        out.setdefault(inv.get(t) or rotulo(t), set()).add(t)
    return out
