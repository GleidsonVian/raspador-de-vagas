import html
import re
import unicodedata


def norm(s: str) -> str:
    """minusculo e sem acentos."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def slug(termo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(termo)).strip("-")


def tem(termo: str, texto_norm: str) -> bool:
    """termo (como palavra inteira) dentro de um texto ja normalizado."""
    return re.search(r"(?<![a-z0-9])" + re.escape(norm(termo)) + r"(?![a-z0-9+#])", texto_norm) is not None


def html_para_texto(h: str) -> str:
    h = re.sub(r"<\s*(br|/p|/li|/div)\s*/?>", "\n", h or "", flags=re.I)
    h = html.unescape(re.sub(r"<[^>]+>", " ", h))
    h = re.sub(r"[ \t]+", " ", h)
    return re.sub(r"\n\s*\n+", "\n\n", h).strip()
