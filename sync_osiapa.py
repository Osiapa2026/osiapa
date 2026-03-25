#!/usr/bin/env python3
"""
sync_osiapa.py
Descarga archivos faltantes de https://osiapa.com/transparencia/
comparando solo por nombre (sin analizar contenido).
Aplica estilo completo (Opción C): Title Case, tildes, sin guiones bajos, truncado a 100 chars.
"""

import os
import re
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSPARENCIA_DIR = os.path.join(BASE_DIR, "transparencia")
LOG_FILE = os.path.join(BASE_DIR, "sync_osiapa_log.txt")
SITIO_URL = "https://osiapa.com/transparencia/"
MAX_NAME_LEN = 100  # máximo de chars para el nombre sin extensión

# Palabras que NO van en Title Case (artículos, preposiciones, conjunciones)
MINUSCULAS = {
    "a", "al", "ante", "bajo", "cabe", "con", "contra", "de", "del",
    "desde", "durante", "e", "el", "en", "entre", "hacia", "hasta",
    "la", "las", "lo", "los", "mediante", "ni", "o", "para", "por",
    "que", "se", "sin", "sobre", "su", "sus", "tras", "u", "un", "una",
    "unos", "unas", "y", "ya",
}

# Correcciones de tipografía conocidas: original_lower → correcto
CORRECCIONES_TYPO = {
    "direcrorio": "Directorio",
    "dorectorio": "Directorio",
    "tansparencia": "Transparencia",
    "ominas": "Nóminas",
    "prodecimiento": "Procedimiento",
}

# Mapa fraccion URL → carpeta en repo
FRACCION_MAP = {
    "Fraccion_I": "Fraccion I",
    "Fraccion_II": "Fraccion II",
    "Fraccion_III": "Fraccion III",
    "Fraccion_IV": "Fraccion IV",
    "Fraccion_V": "Fraccion V",
    "Fraccion_VI": "Fraccion VI",
}


def title_case_es(texto: str) -> str:
    """Title Case respetando artículos/preposiciones en español."""
    palabras = texto.split()
    resultado = []
    for i, p in enumerate(palabras):
        if i == 0:
            resultado.append(p.capitalize())
        elif p.lower() in MINUSCULAS:
            resultado.append(p.lower())
        else:
            resultado.append(p.capitalize())
    return " ".join(resultado)


def aplicar_correcciones(nombre: str) -> str:
    """Corrige errores tipográficos conocidos en el nombre."""
    palabras = nombre.split()
    resultado = []
    for p in palabras:
        lower = p.lower()
        if lower in CORRECCIONES_TYPO:
            resultado.append(CORRECCIONES_TYPO[lower])
        else:
            resultado.append(p)
    return " ".join(resultado)


def transformar_nombre(nombre_raw: str) -> str:
    """
    Transforma el nombre original del archivo a estilo completo (Opción C):
    - Decodifica URL encoding
    - Reemplaza guiones bajos por espacios
    - Aplica correcciones tipográficas
    - Title Case en español
    - Trunca a MAX_NAME_LEN chars (sin extensión) + hash 6 chars si se truncó
    """
    nombre_decoded = unquote(nombre_raw)

    # Separar nombre y extensión
    base, ext = os.path.splitext(nombre_decoded)

    # Reemplazar guiones bajos por espacios
    base = base.replace("_", " ")

    # Normalizar espacios múltiples
    base = re.sub(r"\s+", " ", base).strip()

    # Correcciones tipográficas
    base = aplicar_correcciones(base)

    # Title Case
    base = title_case_es(base)

    # Truncar si es necesario
    if len(base) > MAX_NAME_LEN:
        hash_suffix = "_" + hashlib.md5(base.encode()).hexdigest()[:6]
        base = base[:MAX_NAME_LEN] + hash_suffix

    return base + ext


def obtener_links_sitio(session: requests.Session) -> list[dict]:
    """Extrae todos los links de archivos del sitio de transparencia."""
    resp = session.get(SITIO_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    archivos = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "archivos_transparencia" not in href:
            continue

        # Construir URL absoluta
        if href.startswith("http"):
            url = href
        else:
            url = "http://osiapa.com/" + href.lstrip("/")

        # Extraer partes de la ruta
        partes = url.split("/")
        try:
            idx_art = next(i for i, p in enumerate(partes) if p.startswith("Fraccion_"))
        except StopIteration:
            continue

        if len(partes) < idx_art + 3:
            continue

        fraccion_raw = partes[idx_art]
        subseccion = partes[idx_art + 1] if len(partes) > idx_art + 1 else ""
        nombre_raw = partes[-1]

        if not nombre_raw or "." not in nombre_raw:
            continue

        archivos.append({
            "url": url,
            "fraccion_raw": fraccion_raw,
            "subseccion": subseccion,
            "nombre_raw": unquote(nombre_raw),
        })

    return archivos


def resolver_carpeta_destino(fraccion_raw: str, subseccion: str) -> str | None:
    """
    Mapea fraccion + subseccion del sitio a la carpeta local del repo.
    Busca la carpeta que contenga el nombre de la subsección.
    """
    fraccion_local = FRACCION_MAP.get(fraccion_raw)
    if not fraccion_local:
        return None

    fraccion_dir = os.path.join(TRANSPARENCIA_DIR, fraccion_local)
    if not os.path.isdir(fraccion_dir):
        # Crear la carpeta si no existe
        os.makedirs(fraccion_dir, exist_ok=True)
        return fraccion_dir

    # Buscar subcarpeta que contenga el nombre de la subsección
    subseccion_decoded = unquote(subseccion)
    for entry in os.scandir(fraccion_dir):
        if entry.is_dir() and subseccion_decoded.lower() in entry.name.lower():
            return entry.path

    # Si no se encontró subcarpeta exacta, usar la fracción directamente
    # o crear subcarpeta con el nombre de la subsección
    sub_dir = os.path.join(fraccion_dir, subseccion_decoded)
    os.makedirs(sub_dir, exist_ok=True)
    return sub_dir


def archivos_existentes_en(carpeta: str) -> set[str]:
    """Devuelve set de nombres de archivo (sin path) en la carpeta."""
    if not os.path.isdir(carpeta):
        return set()
    return {f.name for f in os.scandir(carpeta) if f.is_file()}


def main():
    log_lines = []
    descargados = []
    saltados = []
    truncados = []
    errores = []

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; osiapa-sync/1.0)"})

    print("Obteniendo lista de archivos del sitio...")
    try:
        archivos_sitio = obtener_links_sitio(session)
    except Exception as e:
        print(f"ERROR al obtener links: {e}")
        return

    print(f"  {len(archivos_sitio)} archivos encontrados en el sitio.")

    for item in archivos_sitio:
        nombre_raw = item["nombre_raw"]
        nombre_destino = transformar_nombre(nombre_raw)
        base_raw, _ = os.path.splitext(nombre_raw)
        base_dest, _ = os.path.splitext(nombre_destino)
        fue_truncado = len(base_dest) > MAX_NAME_LEN or "_" + hashlib.md5(base_dest.encode()).hexdigest()[:6] in nombre_destino

        carpeta = resolver_carpeta_destino(item["fraccion_raw"], item["subseccion"])
        if not carpeta:
            errores.append(f"[SIN CARPETA] {item['url']}")
            continue

        existentes = archivos_existentes_en(carpeta)

        # Verificar si ya existe (comparar nombre transformado Y nombre raw por si fue descargado antes sin transformar)
        if nombre_destino in existentes or nombre_raw in existentes:
            saltados.append(f"  SKIP  {nombre_destino}")
            continue

        # Descargar
        try:
            r = session.get(item["url"], timeout=60, stream=True)
            if r.status_code != 200:
                errores.append(f"[HTTP {r.status_code}] {item['url']}")
                continue

            ruta_destino = os.path.join(carpeta, nombre_destino)
            with open(ruta_destino, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            descargados.append(f"  OK    {nombre_destino}  →  {carpeta.replace(BASE_DIR, '.')}")
            if nombre_raw != nombre_destino:
                truncados.append(f"  {nombre_raw!r:80s}  →  {nombre_destino!r}")

            print(f"  [+] {nombre_destino}")

        except Exception as e:
            errores.append(f"[EXCEPCION] {item['url']} — {e}")

    # Escribir log
    log_lines.append("=" * 80)
    log_lines.append(f"DESCARGADOS ({len(descargados)})")
    log_lines.append("=" * 80)
    log_lines.extend(descargados)
    log_lines.append("")
    log_lines.append("=" * 80)
    log_lines.append(f"SALTADOS — YA EXISTIAN ({len(saltados)})")
    log_lines.append("=" * 80)
    log_lines.extend(saltados)
    log_lines.append("")
    log_lines.append("=" * 80)
    log_lines.append(f"RENOMBRADOS (original → destino) ({len(truncados)})")
    log_lines.append("=" * 80)
    log_lines.extend(truncados)
    log_lines.append("")
    log_lines.append("=" * 80)
    log_lines.append(f"ERRORES ({len(errores)})")
    log_lines.append("=" * 80)
    log_lines.extend(errores)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    print(f"\nResumen: {len(descargados)} descargados, {len(saltados)} ya existían, {len(errores)} errores.")
    print(f"Log: {LOG_FILE}")


if __name__ == "__main__":
    main()
