import html as html_lib
import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Búsqueda en Fincaraíz: SOLO apartamentos en localidades del norte/noroccidente (HOY y AYER)
URLS_FINCARAIZ = [
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-hoy?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-hoy/pagina2?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-ayer?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-ayer/pagina2?&IDmoneda=4",
]

# Parámetros de filtrado
MAX_PRESUPUESTO = 2800000
MIN_CANON_REAL = 1000000
MIN_AREA_M2 = 58.0
MIN_HABITACIONES = 2

# Barrios estrictamente dentro del polígono
BARRIOS_PERMITIDOS = [
    # Suba Oriental (al sur de Colina/153 y oriente de Av. Boyacá / Titán Plaza)
    "colina campestre", "la colina", "gratamira", "iberia", "ciudad jardin norte",
    "covadonga", "mazuren", "prado veraniego", "prado pinzon", "alcala",
    "nueva autopista", "tierra linda", "spring", "sotileza", "niza", "niza norte",
    "niza suba", "niza antigua", "puente largo", "pasadena", "alhambra",
    "la alhambra", "batan", "el batan", "malibu", "monaco", "provenza",
    "santa rosa", "morato", "pontevedra", "potosi", "julio florez", "andes norte",
    "los andes", "rionegro", "la floresta", "floresta", "cafam floresta",
    "santa maria del lago",
    # Usaquén (entre Autopista Norte y Carrera 7, desde Bosques de Pinos hasta Calle 100)
    "bosques de pinos", "barrancas", "barrancas norte", "cedritos", "los cedros",
    "cedro", "cedro golf", "cedro bolivar", "cedro salazar", "acacias",
    "las acacias", "las margaritas", "caobos", "caobos salazar", "capri",
    "contador", "el contador", "bella suiza", "ginebra", "hacienda santa barbara",
    "santa ana", "santa ana occidental", "santa ana oriental", "santa barbara",
    "santa barbara central", "santa barbara occidental", "santa barbara alta",
    "santa barbara oriental", "unicentro", "multicentro", "la carolina",
    "country club", "el country", "la calleja", "navarra", "san patricio",
    "santa bibiana", "molinos", "molinos norte", "santa paula",
    # Chapinero Norte y Barrios Unidos Norte (Calle 100 hasta Calle 72 / Avenida Chile)
    "chico", "chico norte", "chico reservado", "el chico", "rincon del chico",
    "antiguo country", "el retiro", "el nogal", "la cabrera", "cabrera",
    "parque el virrey", "el virrey", "andino", "el lago", "lago gaitan",
    "porciuncula", "avenida chile", "la castellana", "castellana", "polo club",
    "el polo", "patria", "la patria", "entre rios", "concepcion norte",
]

LOCALIDADES_GENERICAS_URL = {
    "bogota", "usaquen", "suba", "chapinero", "barrios unidos",
    "zona norte", "zona noroccidente", "norte",
}

ARCHIVO_VISTOS = "vistos.json"
ARCHIVO_DESCARTADOS = "descartados.json"


def cargar_json_set(ruta):
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_json_set(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(sorted(list(datos)), f, indent=2)


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=15)
    return resp.status_code == 200 and resp.json().get("ok", False)


def bloquear_recursos(route):
    if route.request.resource_type in ["image", "media", "font"]:
        route.abort()
    else:
        route.continue_()


def normalizar_texto(texto):
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    t = texto.lower().strip()
    for k, v in reemplazos.items():
        t = t.replace(k, v)
    return t


def limpiar_monto(texto_monto):
    solo_digitos = re.sub(r"[^\d]", "", str(texto_monto))
    return int(solo_digitos) if solo_digitos else 0


def formatear_pesos(valor):
    return f"${valor:,.0f}".replace(",", ".")


def extraer_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def buscar_claves_json(obj, claves_objetivo):
    resultados = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in claves_objetivo and isinstance(v, (int, float, str, bool)):
                resultados.append((k, v))
            else:
                resultados.extend(buscar_claves_json(v, claves_objetivo))
    elif isinstance(obj, list):
        for item in obj:
            resultados.extend(buscar_claves_json(item, claves_objetivo))
    return resultados


def extraer_ubicaciones_asociadas(page, next_data):
    """Extrae los elementos del campo 'Ubicaciones asociadas' del aviso."""
    ubicaciones = []

    try:
        items_dom = page.evaluate(
            """() => {
                const elementos = Array.from(document.querySelectorAll('h2, h3, h4, p, span, div, strong'));
                const titulo = elementos.find(el =>
                    el.children.length === 0 &&
                    /ubicaciones asociadas/i.test(el.textContent || '')
                );
                if (!titulo) return [];
                let contenedor = titulo.parentElement;
                for (let i = 0; i < 3 && contenedor; i++) {
                    const enlaces = Array.from(contenedor.querySelectorAll('a, li, span'))
                        .map(n => (n.textContent || '').trim())
                        .filter(t => t && !/ubicaciones asociadas/i.test(t));
                    if (enlaces.length > 0) return enlaces;
                    contenedor = contenedor.parentElement;
                }
                return [];
            }"""
        )
        if items_dom:
            ubicaciones.extend(items_dom)
    except Exception:
        pass

    if next_data:
        def recorrer_ubicaciones(obj):
            if isinstance(obj, dict):
                if "locations" in obj and isinstance(obj["locations"], (dict, list)):
                    pares = buscar_claves_json(obj["locations"], {"name", "label", "slug"})
                    for _, val in pares:
                        if isinstance(val, str):
                            ubicaciones.append(val)
                for k, v in obj.items():
                    if k in {"neighbourhood", "neighborhood", "barrio", "zone"} and isinstance(v, str):
                        ubicaciones.append(v)
                    elif isinstance(v, (dict, list)):
                        recorrer_ubicaciones(v)
            elif isinstance(obj, list):
                for item in obj:
                    recorrer_ubicaciones(item)

        recorrer_ubicaciones(next_data)

    return [normalizar_texto(u) for u in ubicaciones if u and len(u.strip()) >= 3]


def evaluar_ubicacion(ubicaciones_asociadas, link):
    barrio_detectado = None
    es_permitido = False

    for ub in ubicaciones_asociadas:
        for barrio in BARRIOS_PERMITIDOS:
            if re.search(rf"\b{re.escape(barrio)}\b", ub):
                es_permitido = True
                barrio_detectado = ub.title()
                break
        if es_permitido:
            break

    if not barrio_detectado:
        ignorados = {"colombia", "bogota", "bogota, d.c.", "bogota d.c.", "cundinamarca", "zona norte", "zona noroccidente"}
        especificos = [u for u in ubicaciones_asociadas if u not in ignorados]
        if especificos:
            barrio_detectado = especificos[0].title()
        else:
            m_slug = re.search(r"/apartamento-en-arriendo-en-(.+)-bogota/", link)
            barrio_detectado = m_slug.group(1).replace("-", " ").title() if m_slug else "No especificado"

    return es_permitido, barrio_detectado


def extraer_precio(html, texto_ficha, next_data):
    canon_val = 0
    admin_val = 0
    admin_incluida = False

    cabecera = texto_ficha[:3200]
    for m in re.finditer(r"\$\s?([\d\.,]{7,14})(?!\s*/\s*m)", cabecera):
        inicio = max(0, m.start() - 30)
        if "administraci" in cabecera[inicio:m.start()].lower():
            continue
        val = limpiar_monto(m.group(1))
        if MIN_CANON_REAL <= val <= 500000000:
            canon_val = val
            break

    match_admin = re.search(
        r"administraci[oó]n[^\$\d]{0,30}\$\s?([\d\.,]{5,12})",
        cabecera,
        re.IGNORECASE,
    )
    if match_admin:
        cand_admin = limpiar_monto(match_admin.group(1))
        if 50000 <= cand_admin <= 50000000 and cand_admin != canon_val:
            admin_val = cand_admin

    if next_data:
        pares = buscar_claves_json(
            next_data,
            {
                "rentalPrice", "rentPrice", "canon", "price", "value",
                "administration", "adminPrice", "commonExpenses",
                "administrationPrice", "administrationIncluded",
            },
        )
        for k, v in pares:
            if k == "administrationIncluded" and v is True:
                admin_incluida = True
                continue
            if isinstance(v, bool):
                continue
            num = limpiar_monto(v) if isinstance(v, str) else int(float(v))
            if k in {"administration", "adminPrice", "commonExpenses", "administrationPrice"}:
                if 50000 <= num <= 50000000 and admin_val == 0 and num != canon_val:
                    admin_val = num
            elif k in {"rentalPrice", "rentPrice", "canon", "price", "value"}:
                if MIN_CANON_REAL <= num <= 500000000 and canon_val == 0:
                    canon_val = num

    if not admin_incluida:
        admin_incluida = bool(
            re.search(
                r"administraci[oó]n\s+incluida|incluye\s+administraci[oó]n|con\s+administraci[oó]n\s+incluida|gastos\s+comunes\s+incluidos",
                cabecera,
                re.IGNORECASE,
            )
        )

    return canon_val, admin_val, admin_incluida


def extraer_area(texto_ficha, next_data):
    """Prioridad 1: Área Privada. Prioridad 2: Área (construida) solo si no trae Área Privada."""
    m_priv = re.search(r"[ÁA]rea\s+privada[^\d]{0,15}(\d+(?:[.,]\d+)?)", texto_ficha, re.IGNORECASE)
    if m_priv:
        val = float(m_priv.group(1).replace(",", "."))
        if val > 0:
            return val, m_priv.group(1).replace(",", ".")

    if next_data:
        for _, v in buscar_claves_json(next_data, {"privateArea", "areaPrivada", "private_area"}):
            try:
                val = float(str(v).replace(",", "."))
                if 20.0 <= val <= 1000.0:
                    return val, f"{val:g}"
            except ValueError:
                pass

    m_const = re.search(r"[ÁA]rea(?:\s+construida|\s+total)?[^\d]{0,15}(\d+(?:[.,]\d+)?)", texto_ficha, re.IGNORECASE)
    if m_const:
        val = float(m_const.group(1).replace(",", "."))
        if 20.0 <= val <= 1000.0:
            return val, m_const.group(1).replace(",", ".")

    areas_m2 = [
        float(a.replace(",",