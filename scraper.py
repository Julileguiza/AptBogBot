import html as html_lib
import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Búsqueda en Fincaraíz: SOLO apartamentos publicados HOY y AYER
URLS_FINCARAIZ = [
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-hoy?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-hoy/pagina2?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-ayer?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/publicado-ayer/pagina2?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/2-o-mas-habitaciones/hasta-2800000/publicado-hoy?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/apartamentos/bogota-dc/2-o-mas-habitaciones/hasta-2800000/publicado-ayer?&IDmoneda=4",
]

# Parámetros de filtrado
MAX_PRESUPUESTO = 2800000
MIN_CANON_REAL = 1000000
MIN_AREA_M2 = 58.0
MIN_HABITACIONES = 2

# Barrios estrictamente dentro del polígono rojo (sin nombres genéricos de país/localidad)
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

ARCHIVO_VISTOS = "vistos.json"


def cargar_vistos():
    if os.path.exists(ARCHIVO_VISTOS):
        with open(ARCHIVO_VISTOS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_vistos(vistos):
    with open(ARCHIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(sorted(list(vistos)), f, indent=2)


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    requests.post(url, json=payload, timeout=15)


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
    solo_digitos = re.sub(r"[^\d]", "", texto_monto)
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


def ubicacion_permitida(ubicaciones_asociadas):
    for ub in ubicaciones_asociadas:
        for barrio in BARRIOS_PERMITIDOS:
            if re.search(rf"\b{re.escape(barrio)}\b", ub):
                return True
    return False


def extraer_precio(html, texto_ficha, next_data):
    canon_val = 0
    admin_val = 0
    admin_incluida = False

    if next_data:
        pares = buscar_claves_json(
            next_data,
            {
                "rentalPrice", "rentPrice", "canon", "price",
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
            try:
                num = int(float(str(v)))
            except ValueError:
                continue
            if k in {"administration", "adminPrice", "commonExpenses", "administrationPrice"}:
                if 50000 <= num < MIN_CANON_REAL and admin_val == 0:
                    admin_val = num
            else:
                if MIN_CANON_REAL <= num <= 20000000 and canon_val == 0:
                    canon_val = num
                elif 50000 <= num < MIN_CANON_REAL and admin_val == 0:
                    admin_val = num

    if canon_val == 0:
        for m in re.finditer(r'"(?:rentalPrice|canon|rentPrice|price|value)"\s*:\s*(\d{7,9})', html):
            cand = int(m.group(1))
            if MIN_CANON_REAL <= cand <= 20000000:
                canon_val = cand
                break

    if canon_val == 0:
        precios_top = re.findall(r"\$\s?([\d\.]{7,11})(?!\s*/\s*m)", texto_ficha[:3200])
        for p_str in precios_top:
            val = limpiar_monto(p_str)
            if MIN_CANON_REAL <= val <= 20000000:
                canon_val = val
                break

    if admin_val == 0:
        match_admin = re.search(
            r"administraci[oó]n[^\$\d]{0,35}\$\s?([\d\.]{4,9})",
            texto_ficha[:4000],
            re.IGNORECASE,
        )
        if match_admin:
            cand = limpiar_monto(match_admin.group(1))
            if 50000 <= cand < MIN_CANON_REAL and cand != canon_val:
                admin_val = cand

    if not admin_incluida:
        admin_incluida = bool(
            re.search(
                r"administraci[oó]n\s+incluida|incluye\s+administraci[oó]n|con\s+administraci[oó]n\s+incluida|gastos\s+comunes\s+incluidos",
                texto_ficha[:3500],
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
                if 20.0 <= val <= 500.0:
                    return val, f"{val:g}"
            except ValueError:
                pass

    m_const = re.search(r"[ÁA]rea(?:\s+construida|\s+total)?[^\d]{0,15}(\d+(?:[.,]\d+)?)", texto_ficha, re.IGNORECASE)
    if m_const:
        val = float(m_const.group(1).replace(",", "."))
        if 20.0 <= val <= 500.0:
            return val, m_const.group(1).replace(",", ".")

    areas_m2 = [
        float(a.replace(",", "."))
        for a in re.findall(r"(\d+(?:[.,]\d+)?)\s?m[²2]", texto_ficha[:3000], re.IGNORECASE)
    ]
    areas_rango = [a for a in areas_m2 if 20.0 <= a <= 500.0]
    if areas_rango:
        val = max(areas_rango)
        return val, f"{val:g}"

    if next_data:
        for _, v in buscar_claves_json(next_data, {"builtArea", "area", "totalArea", "areaConstruida"}):
            try:
                val = float(str(v).replace(",", "."))
                if 20.0 <= val <= 500.0:
                    return val, f"{val:g}"
            except ValueError:
                pass

    return None, None


def extraer_habitaciones(link, texto_ficha, next_data):
    m_url = re.search(r"-(\d+)-habitaci", link, re.IGNORECASE)
    if m_url:
        return int(m_url.group(1))

    if next_data:
        for _, v in buscar_claves_json(next_data, {"rooms", "bedrooms", "habitaciones", "numRooms"}):
            try:
                val = int(float(str(v)))
                if 1 <= val <= 15:
                    return val
            except ValueError:
                pass

    m_txt = re.search(r"(\d+)\s?(?:hab|habitaci|cuarto|alcoba|dormitorio)", texto_ficha, re.IGNORECASE)
    if m_txt:
        return int(m_txt.group(1))

    return None


def extraer_parqueadero(texto_ficha, next_data):
    if next_data:
        for _, v in buscar_claves_json(next_data, {"garages", "parking", "parqueaderos", "parkingSpaces"}):
            try:
                if int(float(str(v))) >= 1:
                    return "Sí"
            except ValueError:
                pass

    if re.search(r"sin\s+(?:parqueadero|garaje)|0\s+(?:parqueadero|garaje)", texto_ficha, re.IGNORECASE):
        return "No"

    if re.search(r"([1-9]\d*)\s?(?:parqueadero|garaje|estacionamiento)|(?:con\s+parqueadero|con\s+garaje|parqueadero\s+propio|parqueadero\s+cubierto)", texto_ficha, re.IGNORECASE):
        return "Sí"

    return "No"


def procesar_aviso(page, link):
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function(
                "() => document.body && document.body.innerText.includes('$')",
                timeout=8000,
            )
        except Exception:
            pass
        page.wait_for_timeout(1500)
    except Exception:
        return None

    html = page.content()
    next_data = extraer_next_data(html)
    texto_completo = page.inner_text("body")
    if len(texto_completo.strip()) < 200:
        return None

    corte_similares = re.split(
        r"Inmuebles similares|Propiedades similares|También te puede interesar|Quienes vieron este inmueble",
        texto_completo,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    texto_ficha = corte_similares[0]

    # FILTRO 1: UBICACIÓN ("Ubicaciones asociadas" ∈ barrios permitidos)
    ubicaciones = extraer_ubicaciones_asociadas(page, next_data)
    if not ubicacion_permitida(ubicaciones):
        print(f"{link} | Motivo: Fuera de barrios permitidos")
        return None

    # FILTRO 2: PRECIO (Canon + Administración <= 2.800.000 COP)
    canon_val, admin_val, admin_incluida = extraer_precio(html, texto_ficha, next_data)
    if canon_val == 0:
        return None

    if admin_incluida:
        total_val = canon_val
        presupuesto_txt = "Dentro del rango"
        admin_txt = "Incluida"
        total_txt = formatear_pesos(total_val)
    else:
        total_val = canon_val + admin_val
        presupuesto_txt = "Con advertencia: verificar valor total"
        admin_txt = formatear_pesos(admin_val) if admin_val > 0 else "No especificada"
        total_txt = formatear_pesos(total_val) if admin_val > 0 else f"{formatear_pesos(canon_val)} (+ administración)"

    if total_val > MAX_PRESUPUESTO:
        print(f"{link} | Motivo: Precio superior al presupuesto")
        return None

    # FILTRO 3: ÁREA (>= 58 m², prioridad Área Privada)
    area_val, area_str = extraer_area(texto_ficha, next_data)
    if area_val is None or area_val < MIN_AREA_M2:
        print(f"{link} | Motivo: Dentro de precio, pero fuera del área solicitada")
        return None

    # FILTRO 4: HABITACIONES (>= 2)
    num_habs = extraer_habitaciones(link, texto_ficha, next_data)
    if num_habs is None or num_habs < MIN_HABITACIONES:
        print(f"{link} | Motivo: Menos de 2 habitaciones")
        return None

    # CAMPO INFORMATIVO: Parqueadero (Sí / No)
    parqueadero_txt = extraer_parqueadero(texto_ficha, next_data)

    h1 = page.locator("h1").first
    titulo = h1.inner_text().strip() if h1.count() > 0 else "Apartamento en arriendo"

    return {
        "titulo": html_lib.escape(titulo[:120]),
        "presupuesto": presupuesto_txt,
        "canon": formatear_pesos(canon_val),
        "admin": admin_txt,
        "total": total_txt,
        "habitaciones": str(num_habs),
        "tamano": area_str,
        "parqueadero": parqueadero_txt,
        "link": link,
    }


def recolectar_apartamentos_fincaraiz(page, url_busqueda):
    enlaces = []
    page.goto(url_busqueda, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3500)

    for _ in range(5):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(700)

    anchors = page.locator("a[href]").all()
    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        link = f"https://www.fincaraiz.com.co{href}" if href.startswith("/") else href
        link_limpio = link.split("?")[0].split("#")[0]

        if any(bad in link_limpio for bad in ["/pagina", "/arriendo/apartamentos", "/dolares", "/hasta-"]):
            continue

        m_id = re.search(r"/apartamento-en-arriendo-[^/]+/(\d{6,})$", link_limpio)
        if m_id:
            prop_id = m_id.group(1)
            if prop_id not in [e[0] for e in enlaces]:
                enlaces.append((prop_id, link_limpio))
        if len(enlaces) >= 50:
            break
    return enlaces


def main():
    vistos = cargar_vistos()
    enlaces_unicos = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="es-CO",
            timezone_id="America/Bogota",
        )
        page = context.new_page()
        page.route("**/*", bloquear_recursos)

        for url_fr in URLS_FINCARAIZ:
            try:
                for prop_id, link in recolectar_apartamentos_fincaraiz(page, url_fr):
                    if prop_id not in enlaces_unicos:
                        enlaces_unicos[prop_id] = link
            except Exception:
                pass

        for prop_id, link in enlaces_unicos.items():
            if prop_id in vistos:
                continue

            apto = procesar_aviso(page, link)
            if apto:
                msg = (
                    f"🏢 <b>Nombre del Apartamento:</b> {apto['titulo']}\n"
                    f"💰 <b>Presupuesto:</b> {apto['presupuesto']}\n"
                    f"💵 <b>Canon:</b> {apto['canon']}\n"
                    f"🧾 <b>Administración:</b> {apto['admin']}\n"
                    f"💳 <b>Total Estimado:</b> {apto['total']}\n"
                    f"🛏 <b>Habitaciones:</b> {apto['habitaciones']}\n"
                    f"📐 <b>Tamaño:</b> {apto['tamano']} m²\n"
                    f"🚗 <b>Parqueadero:</b> {apto['parqueadero']}\n"
                    f"🔗 <b>Publicación (link FincaRaíz):</b> <a href='{apto['link']}'>Ver anuncio</a>"
                )
                enviar_telegram(msg)
                vistos.add(prop_id)
                time.sleep(1)

            time.sleep(0.5)

        browser.close()

    guardar_vistos(vistos)


if __name__ == "__main__":
    main()