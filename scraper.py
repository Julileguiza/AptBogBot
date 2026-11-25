import html as html_lib
import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# 1. Fincaraíz: Prioriza 'publicado-ayer' (últimas 48 horas: ayer y hoy) + respaldo por localidades
URLS_FINCARAIZ = [
    "https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota-dc/2-o-mas-habitaciones/hasta-2800000/m2-desde-60/publicado-ayer?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota-dc/2-o-mas-habitaciones/hasta-2800000/m2-desde-60/publicado-ayer/pagina2?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2800000/m2-desde-60/edificados/publicado-ultimos-7-dias?&IDmoneda=4",
]

# 2. Metrocuadrado (Búsquedas prefiltradas por habitaciones, precio y área)
URLS_METROCUADRADO = [
    "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/zona-norte/2-o-mas-habitaciones/hasta-2.8-millones/desde-60-m2/?search=form",
    "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/zona-noroccidente/2-o-mas-habitaciones/hasta-2.8-millones/desde-60-m2/?search=form",
    "https://www.metrocuadrado.com/apartamento/arriendo/bogota/2.8-millones--m2/?search=form",
]

# Criterios de filtrado y presupuesto
MIN_CANON_REAL = 1150000
PRESUPUESTO_OBJETIVO = 2700000
MAX_TOTAL_EXTREMO = 2800000
MIN_HABITACIONES = 2
MIN_AREA_M2 = 59.0
FECHA_CORTE = "2026-11-24"  # Permite anuncios de ayer (24) y hoy (25) en adelante

# Polígono de ubicación exacto (incluye Colina/Portales del Monte, Calle 153 y Bella Suiza/Sta. Bárbara)
POLIGONO_ZONA = [
    (4.750000, -74.069078),  # Superior Izquierda (Pinar / Colina Campestre / Portales del Monte)
    (4.742000, -74.021646),  # Superior Derecha (Bosques de Pinos / Calle 161 con Cra. 7)
    (4.653406, -74.038000),  # Inferior Derecha (Avenida Chile / Carrera 7 con Calle 72)
    (4.698282, -74.093596),  # Inferior Izquierda (Titán Plaza / Av. Boyacá con Calle 80)
]

# Respaldo de barrios válidos dentro del polígono (se evalúa SOLO sobre URL y Título H1)
BARRIOS_PERMITIDOS = [
    "cedritos", "cedro", "cedro golf", "bella suiza", "bosques de pinos", "barrancas",
    "santa barbara", "usaquen", "contador", "lisboa", "caobos", "capri", "molinos",
    "chico", "antiguo country", "cabrera", "colina campestre", "colina", "colinas de suba",
    "mazuren", "prado veraniego", "spring", "sotileza", "niza", "puente largo", "pasadena",
    "alhambra", "batan", "malibu", "monaco", "santa rosa", "morato", "pontevedra",
    "julio florez", "andes norte", "los andes", "rionegro", "santa maria del lago",
    "castellana", "polo club", "patria", "rincon del chico", "entre rios", "simon bolivar",
    "metropolis", "modelo norte", "alcazares", "las ferias", "gilmar", "cantagallo",
    "victoria norte", "verbenal", "toberin", "san cristobal norte", "santa teresa",
    "acacias", "las margaritas", "las orquideas", "del monte", "portales del monte",
    "la calleja", "iberia", "gratamira", "ciudad jardin norte", "san patricio", "navarra",
    "remanzo", "canaima", "mirandela", "villa del prado", "nueva zelandia",
    "portales del norte", "britalia", "san jose del prado", "el plan", "atenas",
    "santa helena", "quinta camacho", "porciuncula", "el lago", "153"
]

# Barrios/localidades excluidos (se evalúa SOLO sobre URL y Título H1)
BARRIOS_EXCLUIDOS = [
    "engativa", "el muelle", "fontibon", "bosa", "kennedy", "puente aranda", "subazar",
    "tibabuyes", "el poa", "la gaitana", "bilbao", "compartir", "rincon de suba", "auren",
    "villas de granada", "gran granada", "ciudad salitre", "salitre", "galerias", "teusaquillo",
    "la soledad", "chapinero alto", "chapinero central", "rosales", "pardo rubio", "marly",
    "centro internacional", "candelaria", "san cristobal sur", "usme", "tunjuelito", "rafael uribe",
    "soacha", "mosquera", "funza", "chia", "cajica", "cota", "madrid", "veraguas", "castilla",
    "la igualdad", "quesada", "normandia", "aeropuerto", "modelia", "hayuelos"
]

ARCHIVO_VISTOS = "vistos.json"


def cargar_vistos():
    if os.path.exists(ARCHIVO_VISTOS):
        with open(ARCHIVO_VISTOS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_vistos(vistos):
    with open(ARCHIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(list(vistos), f, indent=2)


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


def limpiar_monto(texto_monto):
    solo_digitos = re.sub(r"[^\d]", "", texto_monto)
    return int(solo_digitos) if solo_digitos else 0


def formatear_pesos(valor):
    return f"${valor:,.0f}".replace(",", ".")


def punto_en_poligono(lat, lon, poligono):
    dentro = False
    j = len(poligono) - 1
    for i in range(len(poligono)):
        yi, xi = poligono[i]
        yj, xj = poligono[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi):
            dentro = not dentro
        j = i
    return dentro


def normalizar_texto(texto):
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    t = texto.lower()
    for k, v in reemplazos.items():
        t = t.replace(k, v)
    return t


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
            if k in claves_objetivo and isinstance(v, (int, float, str)):
                resultados.append((k, v))
            else:
                resultados.extend(buscar_claves_json(v, claves_objetivo))
    elif isinstance(obj, list):
        for item in obj:
            resultados.extend(buscar_claves_json(item, claves_objetivo))
    return resultados


def extraer_enlaces_mc_de_texto(texto, encontrados):
    for m in re.finditer(r"(/inmueble/[^/\"'\s\?]+/\d+-M\d+)", texto):
        ruta = m.group(1)
        link_limpio = f"https://www.metrocuadrado.com{ruta}"
        slug = ruta.rstrip("/").split("/")[-1]
        prop_id = f"mc_{slug}"
        if prop_id not in encontrados:
            encontrados[prop_id] = link_limpio


def es_publicacion_reciente(html, next_data=None):
    fechas_encontradas = []
    if next_data:
        pares_fecha = buscar_claves_json(
            next_data,
            {"createdAt", "updatedAt", "publicationDate", "publishedAt", "dateCreated", "modificationDate"},
        )
        for _, v in pares_fecha:
            m = re.search(r"(202\d-\d{2}-\d{2})", str(v))
            if m:
                fechas_encontradas.append(m.group(1))

    if not fechas_encontradas:
        for m in re.finditer(r'"(?:createdAt|updatedAt|publicationDate|publishedAt|dateCreated)"\s*:\s*"(202\d-\d{2}-\d{2})', html):
            fechas_encontradas.append(m.group(1))

    if fechas_encontradas:
        fecha_mas_reciente = max(fechas_encontradas)
        return fecha_mas_reciente >= FECHA_CORTE, fecha_mas_reciente
    return True, "N/D"


def obtener_coordenadas(html, next_data=None):
    if next_data:
        coords = dict(buscar_claves_json(next_data, {"lat", "lon", "lng", "latitude", "longitude"}))
        lat_v = coords.get("latitude") or coords.get("lat")
        lon_v = coords.get("longitude") or coords.get("lon") or coords.get("lng")
        if lat_v and lon_v:
            try:
                lat_f, lon_f = float(lat_v), float(lon_v)
                if 4.0 < lat_f < 5.5 and -75.0 < lon_f < -73.0:
                    return lat_f, lon_f
            except ValueError:
                pass

    lat_match = re.search(r'"(?:latitude|lat)"\s*:\s*(-?\d+\.\d+)', html)
    lon_match = re.search(r'"(?:longitude|lng|lon)"\s*:\s*(-?\d+\.\d+)', html)
    if lat_match and lon_match:
        lat, lon = float(lat_match.group(1)), float(lon_match.group(1))
        if 4.0 < lat < 5.5 and -75.0 < lon < -73.0:
            return lat, lon
    return None, None


def extraer_canon_y_admin(html, texto_principal, next_data=None):
    canon_val = 0
    admin_val = 0

    if next_data:
        pares = buscar_claves_json(
            next_data,
            {"rentalPrice", "rentPrice", "canon", "price", "administration", "adminPrice", "commonExpenses", "administrationPrice"},
        )
        canones_candidatos = []
        admins_candidatos = []
        for k, v in pares:
            try:
                num = int(float(str(v)))
            except ValueError:
                continue
            if k in {"administration", "adminPrice", "commonExpenses", "administrationPrice"}:
                if 50000 <= num < MIN_CANON_REAL:
                    admins_candidatos.append(num)
            else:
                if MIN_CANON_REAL <= num <= 15000000:
                    canones_candidatos.append(num)
                elif 50000 <= num < MIN_CANON_REAL:
                    admins_candidatos.append(num)

        if canones_candidatos:
            canon_val = canones_candidatos[0]
        if admins_candidatos:
            admin_val = admins_candidatos[0]

    if canon_val == 0:
        for m in re.finditer(r'"(?:rentalPrice|canon|rentPrice|price|value)"\s*:\s*(\d{7,9})', html):
            cand = int(m.group(1))
            if MIN_CANON_REAL <= cand <= 15000000:
                canon_val = cand
                break

    if canon_val == 0:
        precios_top = re.findall(r"\$\s?([\d\.]{7,11})(?!\s*/\s*m)", texto_principal[:3200])
        for p_str in precios_top:
            val = limpiar_monto(p_str)
            if MIN_CANON_REAL <= val <= 15000000:
                canon_val = val
                break

    if admin_val == 0:
        json_admin = re.search(r'"(?:administration|adminPrice|commonExpenses|administrationPrice)"\s*:\s*(\d{4,8})', html)
        if json_admin:
            cand_admin = int(json_admin.group(1))
            if 50000 <= cand_admin < MIN_CANON_REAL:
                admin_val = cand_admin

    if admin_val == 0:
        match_admin = re.search(r"administraci[oó]n[^\$\d]{0,35}\$\s?([\d\.]{4,9})", texto_principal[:4000], re.IGNORECASE)
        if match_admin:
            cand = limpiar_monto(match_admin.group(1))
            if 50000 <= cand < MIN_CANON_REAL and cand != canon_val:
                admin_val = cand

    admin_incluida = bool(re.search(r"administraci[oó]n\s+incluida|incluye\s+administraci[oó]n|gastos\s+comunes\s+incluidos", texto_principal[:3500], re.IGNORECASE))
    return canon_val, admin_val, admin_incluida


def extraer_datos_detalle(page, link, portal):
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function(
                "() => document.body && document.body.innerText.includes('$') && /hab|m²|m2/i.test(document.body.innerText)",
                timeout=10000,
            )
        except Exception:
            pass
        page.wait_for_timeout(2500)
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(1000)
    except Exception as e:
        print(f"Timeout cargando ({portal}), se reintentará luego: {link} ({e})")
        return None, False

    html = page.content()
    next_data = extraer_next_data(html)

    # 0. Verificar fecha de publicación (>= FECHA_CORTE)
    es_reciente, fecha_pub = es_publicacion_reciente(html, next_data)
    if not es_reciente:
        print(f"Silenciado por fecha anterior a {FECHA_CORTE} ({fecha_pub}): {link}")
        return None, True

    h1 = page.locator("h1").first
    titulo = h1.inner_text().strip() if h1.count() > 0 else "Apartamento en arriendo"

    texto_completo = page.inner_text("body")
    if len(texto_completo.strip()) < 200:
        print(f"Página vacía o bloqueada temporalmente ({portal}), se reintentará luego: {link}")
        return None, False

    corte_similares = re.split(
        r"Inmuebles similares|Propiedades similares|También te puede interesar|Quienes vieron este inmueble",
        texto_completo,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    texto_ficha = corte_similares[0]
    texto_ubicacion_norm = normalizar_texto(f"{link} {titulo}")

    # 1. Descartar barrios o municipios excluidos
    if any(b_exc in texto_ubicacion_norm for b_exc in BARRIOS_EXCLUIDOS):
        print(f"Descartado por barrio excluido ({portal}): {link}")
        return None, True

    # 2. Validar polígono geográfico o barrio permitido
    lat, lon = obtener_coordenadas(html, next_data)
    if lat and lon:
        if not punto_en_poligono(lat, lon, POLIGONO_ZONA):
            print(f"Descartado por ubicación fuera de polígono ({lat}, {lon}): {link}")
            return None, True
        link_maps = f"https://www.google.com/maps?q={lat},{lon}"
    else:
        if not any(b_ok in texto_ubicacion_norm for b_ok in BARRIOS_PERMITIDOS):
            print(f"Descartado por falta de coordenadas y barrio fuera de zona ({portal}): {link}")
            return None, True
        link_maps = None

    # 3. Validar Área privada / construida (>= 59 m²)
    match_area_priv = re.search(r"[ÁA]rea\s+privada[^\d]{0,15}(\d+(?:[.,]\d+)?)", texto_ficha, re.IGNORECASE)
    if match_area_priv:
        area_str = match_area_priv.group(1)
        area_val = float(area_str.replace(",", "."))
    else:
        areas_encontradas = [float(a.replace(",", ".")) for a in re.findall(r"(\d+(?:[.,]\d+)?)\s?m[²2]", texto_ficha[:3000], re.IGNORECASE)]
        areas_validas_rango = [a for a in areas_encontradas if 25.0 <= a <= 400.0]
        if areas_validas_rango:
            area_val = max(areas_validas_rango)
            area_str = f"{area_val:g}"
        else:
            area_val = None
            area_str = None

    if area_val is not None:
        if area_val < MIN_AREA_M2:
            print(f"Descartado por área menor a {MIN_AREA_M2} m² ({area_val}): {link}")
            return None, True
        tamano_fmt = f"{area_str} m²"
    else:
        tamano_fmt = "N/D"

    # 4. Validar Habitaciones (>= 2) revisando URL, __NEXT_DATA__, ficha y meta description
    num_habs = None

    m_url_habs = re.search(r"-(\d+)-habitaci", link, re.IGNORECASE)
    if m_url_habs:
        num_habs = int(m_url_habs.group(1))

    if num_habs is None and next_data:
        pares_habs = buscar_claves_json(next_data, {"rooms", "bedrooms", "habitaciones", "numRooms"})
        for _, v in pares_habs:
            try:
                val_h = int(float(str(v)))
                if 1 <= val_h <= 10:
                    num_habs = val_h
                    break
            except ValueError:
                pass

    if num_habs is None:
        m_texto_habs = re.search(r"(\d+)\s?(?:hab|habitaci|cuarto|alcoba|dormitorio)", f"{texto_ficha} {html[:4000]}", re.IGNORECASE)
        if m_texto_habs:
            num_habs = int(m_texto_habs.group(1))

    if num_habs is not None:
        if num_habs < MIN_HABITACIONES:
            print(f"Descartado por menos de {MIN_HABITACIONES} habitaciones ({num_habs}): {link}")
            return None, True
        habs_fmt = str(num_habs)
    else:
        print(f"Descartado por no especificar número de habitaciones: {link}")
        return None, True

    # 5. Validar Canon + Administración (hasta $2.800.000 extremo, sin descartar si falta admin)
    canon_val, admin_val, admin_incluida = extraer_canon_y_admin(html, texto_ficha, next_data)

    if canon_val == 0:
        print(f"Canon no cargado aún ({portal}), se reintentará luego: {link}")
        return None, False

    total_val = canon_val + admin_val
    if total_val > MAX_TOTAL_EXTREMO:
        print(f"Descartado por presupuesto ({formatear_pesos(total_val)} > {formatear_pesos(MAX_TOTAL_EXTREMO)}): {link}")
        return None, True

    advertencias = []
    if total_val > PRESUPUESTO_OBJETIVO:
        advertencias.append("⚠️ <b>PRESUPUESTO EXTREMO (&gt; $2.7M): Evaluar negociación</b>")

    canon_fmt = formatear_pesos(canon_val)
    if admin_incluida:
        admin_fmt = "Incluida"
        total_fmt = canon_fmt
    elif admin_val > 0:
        admin_fmt = formatear_pesos(admin_val)
        total_fmt = formatear_pesos(total_val)
    else:
        admin_fmt = "No especificada"
        total_fmt = f"{canon_fmt} (+ admin por confirmar)"
        advertencias.append("⚠️ <b>Admin no especificada: verificar si está incluida</b>")

    # 6. Parqueadero
    match_parq = re.search(r"(\d+)\s?(?:parqueadero|garaje|estacionamiento)", texto_ficha, re.IGNORECASE)
    if match_parq:
        parqueadero_fmt = f"Sí ({match_parq.group(1)})"
    elif re.search(r"parqueadero|garaje|estacionamiento", texto_ficha, re.IGNORECASE):
        parqueadero_fmt = "Sí"
    else:
        parqueadero_fmt = "No especificado"

    return {
        "portal": portal,
        "titulo": html_lib.escape(titulo[:120]),
        "canon": canon_fmt,
        "admin": admin_fmt,
        "total": total_fmt,
        "habitaciones": habs_fmt,
        "tamano": tamano_fmt,
        "parqueadero": parqueadero_fmt,
        "advertencias": advertencias,
        "link_maps": link_maps,
        "link": link,
    }, True


def recolectar_fincaraiz(page, url_busqueda):
    enlaces = []
    page.goto(url_busqueda, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4500)

    for _ in range(6):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(900)

    anchors = page.locator("a[href]").all()
    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        link = f"https://www.fincaraiz.com.co{href}" if href.startswith("/") else href
        link_limpio = link.split("?")[0].split("#")[0]

        if any(bad in link_limpio for bad in ["/pagina", "/arriendo/casas-y-apartamentos", "/dolares", "/hasta-"]):
            continue

        if re.search(r"/(?:apartamento|casa)-en-arriendo-[^/]+/\d{6,}$", link_limpio):
            slug = link_limpio.rstrip("/").split("/")[-1]
            prop_id = f"fr_{slug}"
            if prop_id not in [e[0] for e in enlaces]:
                enlaces.append((prop_id, link_limpio))
        if len(enlaces) >= 50:
            break
    return enlaces


def recolectar_metrocuadrado(page):
    encontrados = {}

    def interceptar_respuesta(response):
        try:
            if "metrocuadrado.com" in response.url and response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct or "text" in ct:
                    cuerpo = response.text()
                    if "/inmueble/" in cuerpo:
                        extraer_enlaces_mc_de_texto(cuerpo, encontrados)
        except Exception:
            pass

    page.on("response", interceptar_respuesta)

    for url_mc in URLS_METROCUADRADO:
        try:
            page.goto(url_mc, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4500)
            for _ in range(5):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(800)
            extraer_enlaces_mc_de_texto(page.content(), encontrados)
        except Exception as e:
            print(f"Aviso navegando Metrocuadrado: {e}")

    page.remove_listener("response", interceptar_respuesta)
    print(f"Metrocuadrado: {len(encontrados)} candidatos únicos recolectados.")
    return list(encontrados.items())


def main():
    vistos = cargar_vistos()
    nuevos_validos = []

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

        # 1. Fincaraíz (publicado-ayer págs 1-2 + respaldo últimos 7 días en localidades)
        for url_fr in URLS_FINCARAIZ:
            try:
                candidatos_fr = recolectar_fincaraiz(page, url_fr)
                print(f"Fincaraíz: {len(candidatos_fr)} enlaces candidatos detectados.")
                for prop_id, link in candidatos_fr:
                    if prop_id in vistos:
                        continue
                    datos, marcar_visto = extraer_datos_detalle(page, link, "Fincaraíz")
                    if marcar_visto:
                        vistos.add(prop_id)
                    if datos:
                        nuevos_validos.append(datos)
                    time.sleep(0.8)
            except Exception as e:
                print(f"Error en Fincaraíz: {e}")

        # 2. Metrocuadrado
        try:
            candidatos_mc = recolectar_metrocuadrado(page)
            for prop_id, link in candidatos_mc:
                if prop_id in vistos:
                    continue
                datos, marcar_visto = extraer_datos_detalle(page, link, "Metrocuadrado")
                if marcar_visto:
                    vistos.add(prop_id)
                if datos:
                    nuevos_validos.append(datos)
                time.sleep(0.8)
        except Exception as e:
            print(f"Error en Metrocuadrado: {e}")

        browser.close()

    for apto in nuevos_validos:
        bloque_advertencias = ("\n".join(apto["advertencias"]) + "\n") if apto["advertencias"] else ""
        linea_maps = (
            f"📍 <b>Ubicación:</b> <a href='{apto['link_maps']}'>Ver en Google Maps</a>\n"
            if apto["link_maps"]
            else "📍 <b>Ubicación:</b> Zona dentro de polígono (sin pin exacto)\n"
        )
        msg = (
            f"🏢 <b>Apartamento ({apto['portal']}):</b> {apto['titulo']}\n"
            f"{bloque_advertencias}"
            f"💰 <b>Canon:</b> {apto['canon']}\n"
            f"🧾 <b>Administración:</b> {apto['admin']}\n"
            f"💵 <b>Total estimado:</b> {apto['total']}\n"
            f"🛏 <b>Habitaciones:</b> {apto['habitaciones']}\n"
            f"📐 <b>Tamaño:</b> {apto['tamano']}\n"
            f"🚗 <b>Parqueadero:</b> {apto['parqueadero']}\n"
            f"{linea_maps}"
            f"🔗 <b>Publicación:</b> <a href='{apto['link']}'>Ver anuncio</a>"
        )
        enviar_telegram(msg)
        time.sleep(1)

    guardar_vistos(vistos)
    print(f"Proceso finalizado. Enviados: {len(nuevos_validos)}")


if __name__ == "__main__":
    main()