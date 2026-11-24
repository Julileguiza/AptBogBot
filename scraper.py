import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# URLs configuradas con tus filtros
URL_FINCARAIZ = "https://www.fincaraiz.com.co/arriendo/casas-y-apartamentos/bogota-dc/bogota-y-en-usaquen-y-en-zona-norte/2-o-mas-habitaciones/hasta-2700000/m2-desde-60/edificados/publicado-ultimos-7-dias?&IDmoneda=4"
URL_METROCUADRADO = "https://www.metrocuadrado.com/apartamento-casa/arriendo/bogota/2.7-millones--m2/?search=form"

# Tus criterios de filtrado
MAX_TOTAL = 2700000
MIN_HABITACIONES = 2
MIN_AREA_M2 = 59.0

# Polígono ampliado cubriendo las esquinas completas (incluye Cedritos, Polo Club, Alhambra, Colina, Chicó, Mazurén)
POLIGONO_ZONA = [
    (4.745853, -74.089036),  # Nor-Occidente (Suba / Av. Boyacá con Calle 165)
    (4.663533, -74.089036),  # Sur-Occidente (Av. Boyacá con Calle 72)
    (4.663533, -74.026123),  # Sur-Oriente (Carrera 7 con Calle 72)
    (4.745853, -74.026123),  # Nor-Oriente (Carrera 7 con Calle 165)
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
    lat_match = re.search(r'"latitude"\s*:\s*(-?\d+\.\d+)', html)
    lon_match = re.search(r'"longitude"\s*:\s*(-?\d+\.\d+)', html)
    if lat_match and lon_match:
        return float(lat_match.group(1)), float(lon_match.group(1))
    return None, None


def extraer_canon_y_admin(html, texto_principal):
    # 1. Intentar leer el precio exacto desde el JSON estructurado del inmueble (evita confundir con valor m² o similares)
    canon_val = 0
    json_price = re.search(r'"price"\s*:\s*(\d{6,9})', html)
    if json_price:
        canon_val = int(json_price.group(1))

    if canon_val == 0:
        # Si no está en JSON, buscar montos en la parte superior de la ficha (primeros 2500 caracteres)
        precios_top = re.findall(r"\$\s?([\d\.]{6,11})(?!\s*/\s*m)", texto_principal[:2500])
        for p_str in precios_top:
            val = limpiar_monto(p_str)
            if 500000 <= val <= 15000000:
                canon_val = val
                break

    # 2. Buscar administración
    admin_val = 0
    json_admin = re.search(r'"(?:administration|adminPrice|commonExpenses)"\s*:\s*(\d{4,8})', html)
    if json_admin:
        admin_val = int(json_admin.group(1))
    else:
        match_admin = re.search(r"administraci[oó]n[^\$\d]{0,25}\$\s?([\d\.]{4,9})", texto_principal[:3500], re.IGNORECASE)
        if match_admin:
            cand = limpiar_monto(match_admin.group(1))
            if cand != canon_val and cand < canon_val:
                admin_val = cand

    admin_incluida = bool(re.search(r"administraci[oó]n\s+incluida|incluye\s+administraci[oó]n", texto_principal[:3500], re.IGNORECASE))
    return canon_val, admin_val, admin_incluida


def extraer_datos_detalle(page, link, portal):
    page.goto(link, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    html = page.content()

    # 1. Validar polígono geográfico
    lat, lon = obtener_coordenadas(html)
    if lat and lon:
        if not punto_en_poligono(lat, lon, POLIGONO_ZONA):
            print(f"Descartado por ubicación fuera de polígono ({lat}, {lon}): {link}")
            return None
        link_maps = f"https://www.google.com/maps?q={lat},{lon}"
    else:
        link_maps = None

    # Tomamos solo la ficha principal (antes de "Inmuebles similares" o "Recomendados")
    texto_completo = page.inner_text("body")
    corte_similares = re.split(r"Inmuebles similares|Propiedades similares|También te puede interesar", texto_completo, maxsplit=1, flags=re.IGNORECASE)
    texto_ficha = corte_similares[0]

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
    match_habs = re.search(r"(\d+)\s?(?:hab|habitaci|cuarto|alcoba)", texto_ficha, re.IGNORECASE)
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
    if admin_incluida:
        admin_fmt = "Incluida"
        total_fmt = canon_fmt
    elif admin_val > 0:
        admin_fmt = formatear_pesos(admin_val)
        total_fmt = formatear_pesos(total_val)
    else:
        admin_fmt = "No especificada"
        total_fmt = canon_fmt

    # 5. Parqueadero
    match_parq = re.search(r"(\d+)\s?(?:parqueadero|garaje)", texto_ficha, re.IGNORECASE)
    if match_parq:
        parqueadero_fmt = f"Sí ({match_parq.group(1)})"
    elif re.search(r"parqueadero|garaje", texto_ficha, re.IGNORECASE):
        parqueadero_fmt = "Sí"
    else:
        parqueadero_fmt = "No especificado"

    # 6. Título
    h1 = page.locator("h1").first
    titulo = h1.inner_text().strip() if h1.count() > 0 else "Apartamento en arriendo"

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

    # En Metrocuadrado, hacer clic en el desplegable de orden para seleccionar "Más reciente"
    if prefijo == "mc":
        try:
            selector_orden = page.locator("text=Más relevante").first
            if selector_orden.count() > 0:
                selector_orden.click()
                page.wait_for_timeout(1000)
                page.locator("text=Más reciente").first.click()
                page.wait_for_timeout(3500)
                print("Metrocuadrado: Ordenado por 'Más reciente' correctamente.")
        except Exception as e:
            print(f"Metrocuadrado: No se pudo cambiar orden automático ({e})")

    # Hacer 4 scrolls para cargar más tarjetas en pantalla
    for _ in range(4):
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1500)

    anchors = page.locator("a[href*='/inmueble/'], a[href*='/apartamento'], a[href*='/casa']").all()
    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        link = f"{dominio}{href}" if href.startswith("/") else href
        link_limpio = link.split("?")[0]
        if "/pagina" in link_limpio:
            continue
        if re.search(r"[-/]\d{5,}|[-/]M\d+", link_limpio):
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

        portales = [
            (URL_FINCARAIZ, "https://www.fincaraiz.com.co", "fr", "Fincaraíz"),
            (URL_METROCUADRADO, "https://www.metrocuadrado.com", "mc", "Metrocuadrado"),
        ]

        for url_busq, dominio, prefijo, nombre_portal in portales:
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
                    time.sleep(1.2)
            except Exception as e:
                print(f"Error en {nombre_portal}: {e}")

        browser.close()

    for apto in nuevos_validos:
        linea_maps = (
            f"📍 <b>Ubicación:</b> <a href='{apto['link_maps']}'>Ver en Google Maps</a>\n"
            if apto["link_maps"]
            else "📍 <b>Ubicación:</b> Coordenadas no publicadas\n"
        )
        msg = (
            f"🏢 <b>Apartamento:</b> {apto['titulo']}\n"
            f"💰 <b>Canon:</b> {apto['canon']}\n"
            f"🧾 <b>Administración:</b> {apto['admin']}\n"
            f"💵 <b>Total estimado:</b> {apto['total']}\n"
            f"🛏 <b>Habitaciones:</b> {apto['habitaciones']}\n"
            f"📐 <b>Tamaño:</b> {apto['tamano']}\n"
            f"🚗 <b>Parqueadero:</b> {apto['parqueadero']}\n"
            f"{linea_maps}"
            f"🔗 <b>Publicación:</b> <a href='{apto['link']}'>Ver en {apto['portal']}</a>"
        )
        enviar_telegram(msg)
        time.sleep(1)

    guardar_vistos(vistos)
    print(f"Proceso finalizado. Enviados: {len(nuevos_validos)}")


if __name__ == "__main__":
    main()