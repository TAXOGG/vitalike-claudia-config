"""
sync_catalogo.py — Sincroniza el catálogo de Vitalike CR con Claudia

Uso:
  python sync_catalogo.py

Credenciales en variables de entorno o archivo .env:
  WC_URL      — URL de la tienda, ej: https://vitalikecr.com
  WC_KEY      — Consumer Key de WooCommerce API
  WC_SECRET   — Consumer Secret de WooCommerce API
"""

import base64
import os
import json
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError:
    sys.exit("Falta 'requests'. Instalá con: pip install requests")

# Cargar .env si existe
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

STORE_URL = os.environ.get("WC_URL", "https://vitalikecr.com").rstrip("/")
WC_KEY    = os.environ.get("WC_KEY", "")
WC_SECRET = os.environ.get("WC_SECRET", "")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")
GITHUB_PATH  = "system-prompt.txt"

HERE = Path(__file__).parent
SYSTEM_PROMPT_FILE  = HERE / "system-prompt.txt"
MAKE_BODY_FILE      = HERE / "make-body-ready.txt"



def format_price(price_str: str) -> str:
    try:
        val = int(float(price_str))
        return f"₡{val:,}"
    except (ValueError, TypeError):
        return f"₡{price_str}"


def fetch_all_products() -> list:
    if not WC_KEY or not WC_SECRET:
        sys.exit(
            "ERROR: Faltan credenciales WooCommerce.\n"
            "Definí WC_KEY y WC_SECRET en el archivo .env o como variables de entorno."
        )

    auth = HTTPBasicAuth(WC_KEY, WC_SECRET)
    products = []
    page = 1

    print(f"Consultando WooCommerce en {STORE_URL}...")
    while True:
        resp = requests.get(
            f"{STORE_URL}/wp-json/wc/v3/products",
            params={"per_page": 100, "page": page, "status": "publish"},
            auth=auth,
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        products.extend(batch)
        print(f"  Página {page}: {len(batch)} productos obtenidos")
        if len(batch) < 100:
            break
        page += 1

    return products


def build_products_section(products: list) -> str:
    in_stock = [p for p in products if p.get("stock_status") == "instock"]
    print(f"\nTotal publicados: {len(products)} | En stock: {len(in_stock)}")

    # Agrupar por primera categoría
    grouped: dict[str, list] = {}
    for p in in_stock:
        cats = p.get("categories") or []
        cat = cats[0]["name"] if cats else "SIN CATEGORÍA"
        grouped.setdefault(cat, []).append(p)

    lines = ["# PRODUCTOS"]
    for cat_name, prods in grouped.items():
        lines.append(f"\n## {cat_name.upper()}")
        for p in prods:
            pid = p["id"]
            name = p["name"].upper()
            price = format_price(p.get("price", ""))
            regular = p.get("regular_price", "")
            sale    = p.get("sale_price", "")
            cart_url = f"{STORE_URL}/carrito/?add-to-cart={pid}"

            if sale and regular and sale != regular:
                price_str = f"{format_price(sale)} (normal {format_price(regular)})"
            else:
                price_str = format_price(p.get("price", ""))

            lines.append(f"- {name} | ID: {pid} | {price_str} | {cart_url}")

    return "\n".join(lines)


def split_prompt(text: str):
    """Separa el prompt en (encabezado_fijo, sección_productos, pie_fijo)."""
    prod_match = re.search(r"\n# PRODUCTOS\n", text)
    if not prod_match:
        sys.exit("ERROR: No se encontró '# PRODUCTOS' en system-prompt.txt")

    header = text[: prod_match.start()]

    # El pie empieza en el siguiente encabezado de nivel 1 después de PRODUCTOS
    after_prod = text[prod_match.end():]
    footer_match = re.search(r"\n# [A-Z]", after_prod)
    if footer_match:
        footer = after_prod[footer_match.start():]
    else:
        footer = ""

    return header, footer


def push_to_github(content: str, path: str = GITHUB_PATH):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("\nGITHUB_TOKEN/GITHUB_REPO no configurados — se omite publicación en GitHub.")
        return None

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    repo_resp = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}", headers=headers, timeout=30)
    repo_resp.raise_for_status()
    default_branch = repo_resp.json()["default_branch"]

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    get_resp = requests.get(api_url, headers=headers, params={"ref": default_branch}, timeout=30)
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

    payload = {
        "message": f"Sync catalogo Claudia {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": default_branch,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
    put_resp.raise_for_status()

    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{default_branch}/{path}"
    print(f"Publicado en GitHub: {raw_url}")
    return raw_url


def main():
    products = fetch_all_products()
    new_products_section = build_products_section(products)

    # Leer y reconstruir system-prompt.txt
    original = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")
    header, footer = split_prompt(original)
    new_prompt = header + "\n" + new_products_section + footer

    SYSTEM_PROMPT_FILE.write_text(new_prompt, encoding="utf-8")
    print(f"\nActualizado: {SYSTEM_PROMPT_FILE.name}")

    # Regenerar make-body-ready.txt
    # JSON ya valido y escapado; Make.com solo reemplaza el marcador de texto
    # __MESSAGES_PLACEHOLDER__ por el historial real (evita que Make intente
    # evaluarlo como variable, cosa que si pasaria con {{1.messages_str}} literal)
    system_escaped = json.dumps(new_prompt)[1:-1]  # quitar comillas externas

    make_body = (
        '{"model": "claude-haiku-4-5-20251001", "max_tokens": 800, "system": "'
        + system_escaped
        + '", "messages": __MESSAGES_PLACEHOLDER__}'
    )
    MAKE_BODY_FILE.write_text(make_body, encoding="utf-8")
    print(f"Actualizado: {MAKE_BODY_FILE.name}")

    push_to_github(new_prompt, GITHUB_PATH)
    push_to_github(make_body, "make-body-ready.txt")

    # Resumen
    in_stock_count = sum(1 for p in products if p.get("stock_status") == "instock")
    out_count = len(products) - in_stock_count
    print(f"\nResumen:")
    print(f"  OK: {in_stock_count} productos en stock incluidos en Claudia")
    print(f"  --: {out_count} productos sin stock excluidos")


if __name__ == "__main__":
    main()
