import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# 1. Fincaraíz (Páginas 1 y 2 en localidades de tu polígono)
URLS_FINCARAIZ = [
    "https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2700000/m2-desde-60/edificados/publicado-ultimos-7-dias?&IDmoneda=4",
    "https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota-dc/usaquen-y-en-zona-norte-y-en-suba-y-en-barrios-unidos-y-en-chapinero/2-o-mas-habitaciones/hasta-2700000/m2-desde-60/edificados/publicado-ultimos-7-dias/pagina2?&IDmoneda=4",
]

# 2. Metrocuadrado (Zona Norte, Noroccidente y búsqueda general filtrada)
URLS_METROCUADRADO = [
    "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/zona-norte/2-o-mas-habitaciones/hasta-2.7-millones/desde-60-m2/?search=form",
    "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/zona-noroccidente/2-o-mas-habitaciones/hasta-2.7-millones/desde-60-m2/?search=form",
    "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/2.7-millones--m2/?search=form",
]

# 3. Mercado Libre Inmuebles (Tu recuadro de mapa + orden más reciente)
URLS_MERCADOLIBRE = [
    "https://listado.mercadolibre.com.co/inmuebles/apartamentos/arriendo/_OrderId_BEGINS*DESC_PriceRange_0COP-2700000COP_COVERED*AREA_60m%C2%B2-*_item*location_lat:4.647562088300754*4.722327467335745,lon:-74.10983551020531*-73.98040283198266",
]

# 4. Ciencuadras (Bogotá arriendo hasta $2.7M desde 60 m2)
URLS_CIENCUADRAS = [
    "https://www.ciencuadras.com/arriendo/bogota/apartamento/hasta-2700000/desde-60-m2",
]

# 5. Houm (Suba y Usaquén prefiltrados)
URLS_HOUM = [
    "https://houm.com/co/arriendo-apartamentos-bogota/suba/desde-2-dormitorios?maxRentalPriceTotal=2700000&minArea=60",
    "https://houm.com/co/arriendo-apartamentos-bogota/usaquen/desde-2-dormitorios?maxRentalPriceTotal=2700000&minArea=60",
]

# Criterios de filtrado
MAX_TOTAL = 2700000
MIN_HABITACIONES = 2
MIN_AREA_M2 = 59.0

# Polígono de ubicación exacto
POLIGONO_ZONA = [
    (4.745853, -74.089036),  # Nor-Occidente (Suba / Av. Boyacá con Calle 165)
    (4.663533, -74.089036),  # Sur-Occidente (Av. Boyacá con Calle 72)
    (4.663533, -74.026123),  # Sur-Oriente (Carrera 7 con Calle 72)
    (4.745853, -74.026123),  # Nor-Oriente (Carrera 7 con Calle 165)
]

# Respaldo de barrios válidos dentro del polígono
BARRIOS_PERMITIDOS = [
    "cedritos", "cedro", "bella suiza", "bosques de pinos", "barrancas", "santa barbara",
    "usaquen", "contador", "lisboa", "caobos", "capri", "molinos", "chico", "antiguo country",
    "cabrera", "colina campestre", "colina", "mazuren", "prado veraniego", "spring", "sotileza",
    "niza", "puente largo", "pasadena", "alhambra", "batan", "malibu", "monaco", "santa rosa",
    "morato", "pontevedra", "julio florez", "andes norte", "rionegro", "santa maria del lago",
    "castellana", "polo club", "patria", "rincon del chico", "entre rios", "simon bolivar",
    "metropolis", "modelo norte", "alcazares", "las ferias", "gilmar", "cantagallo", "victoria norte",
    "verbenal", "toberin", "san cristobal norte", "santa teresa", "acacias", "las margaritas",
    "iberia", "gratamira", "ciudad jardin norte", "san patricio", "navarra", "remanzo"
]

# Barrios/localidades excluidos explícitamente
BARRIOS_EXCLUIDOS = [
    "engativa", "el muelle", "fontibon", "bosa", "kennedy", "puente aranda", "subazar",
    "tibabuyes", "el poa", "la gaitana", "bilbao", "compartir", "rincon de suba", "auren",
    "villas de granada", "gran granada", "ciudad salitre", "salitre", "galerias", "teusaquillo",
    "la soledad", "chapinero alto", "chapinero central", "rosales", "pardo rubio", "marly",
    "centro internacional", "candelaria", "san cristobal sur", "usme", "tunjuelito", "rafael uribe",
    "soacha", "mosquera", "funza", "chia", "cajica", "cota", "madrid"
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


def obtener_coordenadas(html):
    # Busca formatos estándar JSON (Fincaraíz, Metrocuadrado __NEXT_DATA__, Mercado Libre, Ciencuadras, Houm)
    lat_match = re.search(r'"(?:latitude|lat)"\s*:\s*(-?\d+\.\d+)', html)
    lon_match = re.search(r'"(?:longitude|lng|lon)"\s*:\s*(-?\d+\.\d+)', html)
    if lat_match and lon_match:
        lat, lon = float(lat_match.group(1)), float(lon_match.group(1))
        if 4.0 < lat < 5.5 and -75.0 < lon < -73.0:
            return lat, lon
    return None, None


def normalizar_texto(texto):
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"}
    t = texto.lower()
    for k, v in reemplazos.items():
        t = t.replace(k, v)
    return t


def extraer_canon_y_admin(html, texto_principal):
    canon_val = 0
    json_price = re.search(r'"(?:price|rentalPrice|canon)"\s*:\s*(\d{6,9})', html)
    if json_price:
        canon_val = int(json_price.group(1))

    if canon_val == 0:
        precios_top = re.findall(r"\$\s?([\d\.]{6,11})(?!\s*/\s*m)", texto_principal[:2500])
        for p_str in precios_top:
            val = limpiar_monto(p_str)
            if 500000 <= val <= 15000000:
                canon_val = val
                break

    admin_val = 0
    json_admin = re.search(r'"(?:administration|adminPrice|commonExpenses|administrationPrice)"\s*:\s*(\d{4,8})', html)
    if json_admin:
        admin_val = int(json_admin.group(1))
    else:
        match_admin = re.search(r"administraci[oó]n[^\$\d]{0,25}\$\s?([\d\.]{4,9})", texto_principal[:3500], re.IGNORECASE)
        if match_admin:
            cand = limpiar_monto(match_admin.group(1))
            if cand != canon_val and cand < canon_val:
                admin_val = cand

    admin_incluida = bool(re.search(r"administraci[oó]n\s+incluida|incluye\s+administraci[oó]n|gastos\s+comunes\s+incluidos", texto_principal[:3500], re.IGNORECASE))
    return canon_val, admin_val, admin_incluida


def extraer_datos_detalle(page, link, portal):
    page.goto(link, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    html = page.content()

    h1 = page.locator("h1").first
    titulo = h1.inner_text().strip() if h1.count() > 0 else "Apartamento en arriendo"

    texto_completo = page.inner_text("body")
    corte_similares = re.split(
        r"Inmuebles similares|Propiedades similares|También te puede interesar|Quienes vieron este inmueble",
        texto_completo,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    texto_ficha = corte_similares[0]
    texto_ubicacion_norm = normalizar_texto(f"{link} {titulo} {texto_ficha[:1200]}")

    # Descartar explícitamente barrios o municipios lejanos
    if any(b_exc in texto_ubicacion_norm for b_exc in BARRIOS_EXCLUIDOS):
        print(f"Descartado por barrio excluido ({portal}): {link}")
        return None

    # 1. Validar polígono geográfico (o respaldo por barrio permitido si no hay GPS)
    lat, lon = obtener_coordenadas(html)
    if lat and lon:
        if not punto_en_poligono(lat, lon, POLIGONO_ZONA):
            print(f"Descartado por ubicación fuera de polígono ({lat}, {lon}): {link}")
            return None
        link_maps = f"https://www.google.com/maps?q={lat},{lon}"
    else:
        if not any(b_ok in texto_ubicacion_norm for b_ok in BARRIOS_PERMITIDOS):
            print(f"Descartado por falta de coordenadas y barrio fuera de zona ({portal}): {link}")
            return None
        link_maps = None

    # 2. Validar Área privada / construida (>= 59 m²)
    match_area_priv = re.search(r"[ÁA]rea\s+privada[^\d]{0,15}(\d+(?:[.,]\d+)?)", texto_ficha, re.IGNORECASE)
    match_area_gen = re.search(r"(\d+(?:[.,]\d+)?)\s?m[²2]", texto_ficha, re.IGNORECASE)
    area_str = match_area_priv.group(1) if match_area_priv else (match_area_gen.group(1) if match_area_gen else None)

    if area_str:
        area_val = float(area_str.replace(",", "."))
        if area_val < MIN_AREA_M2:
            print(f"Descartado por área menor a {MIN_AREA_M2} m² ({area_val}): {link}")
            return None
        tamano_fmt = f"{area_str} m²"
    else:
        tamano_fmt = "N/D"

    # 3. Validar Habitaciones (>= 2)
    match_habs = re.search(r"(\d+)\s?(?:hab|habitaci|cuarto|alcoba|dormitorio)", texto_ficha, re.IGNORECASE)
    if match_habs:
        num_habs = int(match_habs.group(1))
        if num_habs < MIN_HABITACIONES:
            print(f"Descartado por menos de {MIN_HABITACIONES} habitaciones ({num_habs}): {link}")
            return None
        habs_fmt = str(num_habs)
    else:
        habs_fmt = "N/D"

    # 4. Validar Canon + Administración (<= 2.700.000)
    canon_val, admin_val, admin_incluida = extraer_canon_y_admin(html, texto_ficha)
    total_val = canon_val + admin_val

    if canon_val > 0 and total_val > MAX_TOTAL:
        print(f"Descartado por presupuesto ({formatear_pesos(total_val)} > {formatear_pesos(MAX_TOTAL)}): {link}")
        return None

    canon_fmt = formatear_pesos(canon_val) if canon_val > 0 else "Consultar"
    if admin_incluida or portal == "Houm":
        admin_fmt = "Incluida"
        total_fmt = canon_fmt
    elif admin_val > 0:
        admin_fmt = formatear_pesos(admin_val)
        total_fmt = formatear_pesos(total_val)
    else:
        admin_fmt = "No especificada"
        total_fmt = canon_fmt

    # 5. Parqueadero
    match_parq = re.search(r"(\d+)\s?(?:parqueadero|garaje|estacionamiento)", texto_ficha, re.IGNORECASE)
    if match_parq:
        parqueadero_fmt = f"Sí ({match_parq.group(1)})"
    elif re.search(r"parqueadero|garaje|estacionamiento", texto_ficha, re.IGNORECASE):
        parqueadero_fmt = "Sí"
    else:
        parqueadero_fmt = "No especificado"

    return {
        "portal": portal,
        "titulo": titulo[:120],
        "canon": canon_fmt,
        "admin": admin_fmt,
        "total": total_fmt,
        "habitaciones": habs_fmt,
        "tamano": tamano_fmt,
        "parqueadero": parqueadero_fmt,
        "link_maps": link_maps,
        "link": link,
    }


def recolectar_enlaces(page, url_busqueda, dominio, prefijo):
    enlaces = []
    page.goto(url_busqueda, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4500)

    if prefijo == "mc":
        try:
            selector_orden = page.locator("text=Más relevante").first
            if selector_orden.count() > 0:
                selector_orden.click()
                page.wait_for_timeout(1000)
                page.locator("text=Más reciente").first.click()
                page.wait_for_timeout(3500)
        except Exception:
            pass

    for _ in range(5):
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1000)

    anchors = page.locator("a[href]").all()
    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        link = f"{dominio}{href}" if href.startswith("/") else href
        link_limpio = link.split("?")[0].split("#")[0]

        # Ignorar páginas de listado/filtros
        if any(bad in link_limpio for bad in ["/pagina", "/arriendo/casas-y-apartamentos", "/apartamento-casa/arriendo", "/dolares", "/hasta-", "/desde-"]):
            continue

        es_valido = False
        if prefijo == "fr" and re.search(r"/(?:apartamento|casa)-en-arriendo-[^/]+/\d{6,}$", link_limpio):
            es_valido = True
        elif prefijo == "mc" and re.search(r"/inmueble/[^/]+/\d+-M\d+$", link_limpio):
            es_valido = True
        elif prefijo == "ml" and re.search(r"/MCO-\d+", link_limpio):
            es_valido = True
        elif prefijo == "cc" and re.search(r"/inmueble/[^/]+/\d+$", link_limpio):
            es_valido = True
        elif prefijo == "hm" and re.search(r"/co/arriendo-apartamento-[^/]+/\d+$", link_limpio):
            es_valido = True

        if es_valido:
            slug = link_limpio.rstrip("/").split("/")[-1]
            prop_id = f"{prefijo}_{slug}"
            if prop_id not in [e[0] for e in enlaces]:
                enlaces.append((prop_id, link_limpio))
        if len(enlaces) >= 35:
            break
    return enlaces


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

        busquedas = [
            (URLS_FINCARAIZ[0], "https://www.fincaraiz.com.co", "fr", "Fincaraíz"),
            (URLS_FINCARAIZ[1], "https://www.fincaraiz.com.co", "fr", "Fincaraíz"),
            (URLS_METROCUADRADO[0], "https://www.metrocuadrado.com", "mc", "Metrocuadrado"),
            (URLS_METROCUADRADO[1], "https://www.metrocuadrado.com", "mc", "Metrocuadrado"),
            (URLS_METROCUADRADO[2], "https://www.metrocuadrado.com", "mc", "Metrocuadrado"),
            (URLS_MERCADOLIBRE[0], "https://apartamento.mercadolibre.com.co", "ml", "Mercado Libre"),
            (URLS_CIENCUADRAS[0], "https://www.ciencuadras.com", "cc", "Ciencuadras"),
            (URLS_HOUM[0], "https://houm.com", "hm", "Houm"),
            (URLS_HOUM[1], "https://houm.com", "hm", "Houm"),
        ]

        for url_busq, dominio, prefijo, nombre_portal in busquedas:
            try:
                candidatos = recolectar_enlaces(page, url_busq, dominio, prefijo)
                print(f"{nombre_portal}: {len(candidatos)} enlaces candidatos detectados.")
                for prop_id, link in candidatos:
                    if prop_id in vistos:
                        continue
                    vistos.add(prop_id)
                    datos = extraer_datos_detalle(page, link, nombre_portal)
                    if datos:
                        nuevos_validos.append(datos)
                    time.sleep(0.8)
            except Exception as e:
                print(f"Error en {nombre_portal}: {e}")

        browser.close()

    # Solo envía mensajes si encontró nuevos apartamentos válidos; si hay 0, no envía nada
    for apto in nuevos_validos:
        linea_maps = (
            f"📍 <b>Ubicación:</b> <a href='{apto['link_maps']}'>Ver en Google Maps</a>\n"
            if apto["link_maps"]
            else "📍 <b>Ubicación:</b> Zona dentro de polígono (sin pin exacto)\n"
        )
        msg = (
            f"🏢 <b>Apartamento ({apto['portal']}):</b> {apto['titulo']}\n"
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