import html as html_lib
import json
import os
import re
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["9273646103:AAHuszfL591VdsLo8CU_HWl8hr5NFUcyBdo"]
CHAT_ID = os.environ["9287215182"]

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

# Nombres genéricos de localidad en URL que requieren abrir la ficha para confirmar el barrio exacto
LOCALIDADES_GENERICAS_URL = {
    "bogota", "usaquen", "suba", "chapinero", "barrios unidos",
    "zona norte", "zona noroccidente", "norte",
}

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


def evaluar_ubicacion(ubicaciones_asociadas, link):
    """Valida si 'Ubicaciones asociadas' coincide con BARRIOS_PERMITIDOS y retorna el nombre del barrio para el log."""
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
        return None, ("Sin carga (Timeout)", f"Sin carga (Timeout) | {link} | Barrio: N/D | Área: N/D | Precio: N/D")

    html = page.content()
    next_data = extraer_next_data(html)
    texto_completo = page.inner_text("body")
    if len(texto_completo.strip()) < 200:
        return None, ("Sin carga (HTML vacío)", f"Sin carga (HTML vacío) | {link} | Barrio: N/D | Área: N/D | Precio: N/D")

    corte_similares = re.split(
        r"Inmuebles similares|Propiedades similares|También te puede interesar|Quienes vieron este inmueble",
        texto_completo,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    texto_ficha = corte_similares[0]

    # Extraer todos los campos primero para que cualquier log incluya Barrio, Área y Precio
    ubicaciones = extraer_ubicaciones_asociadas(page, next_data)
    es_ubicacion_ok, barrio_log = evaluar_ubicacion(ubicaciones, link)

    canon_val, admin_val, admin_incluida = extraer_precio(html, texto_ficha, next_data)
    if canon_val > 0:
        if admin_incluida:
            total_val = canon_val
            presupuesto_txt = "Dentro del rango"
            admin_txt = "Incluida"
            total_txt = formatear_pesos(total_val)
            precio_log = f"{total_txt} (Admin incluida)"
        else:
            total_val = canon_val + admin_val
            presupuesto_txt = "Con advertencia: verificar valor total"
            admin_txt = formatear_pesos(admin_val) if admin_val > 0 else "No especificada"
            total_txt = formatear_pesos(total_val) if admin_val > 0 else f"{formatear_pesos(canon_val)} (+ administración)"
            precio_log = (
                f"{total_txt} (Canon: {formatear_pesos(canon_val)} + Admin: {admin_txt})"
                if admin_val > 0
                else f"{formatear_pesos(canon_val)} (+ Admin no especificada)"
            )
    else:
        total_val = 0
        precio_log = "No detectado"

    area_val, area_str = extraer_area(texto_ficha, next_data)
    area_log = f"{area_str} m²" if area_str else "N/D"

    num_habs = extraer_habitaciones(link, texto_ficha, next_data)

    # FILTRO 1: UBICACIÓN ("Ubicaciones asociadas" ∈ barrios permitidos)
    if not es_ubicacion_ok:
        cat = "Fuera de barrios permitidos"
        return None, (cat, f"{cat} | {link} | Barrio: {barrio_log} | Área: {area_log} | Precio: {precio_log}")

    # FILTRO 2: PRECIO (Canon + Administración <= 2.800.000 COP)
    if canon_val == 0:
        cat = "Sin precio detectado"
        return None, (cat, f"{cat} | {link} | Barrio: {barrio_log} | Área: {area_log} | Precio: {precio_log}")

    if total_val > MAX_PRESUPUESTO:
        cat = "Precio superior al presupuesto"
        return None, (cat, f"{cat} | {link} | Barrio: {barrio_log} | Área: {area_log} | Precio: {precio_log}")

    # FILTRO 3: ÁREA (>= 58 m², prioridad Área Privada)
    if area_val is None or area_val < MIN_AREA_M2:
        cat = "Fuera del area solicitada"
        return None, (cat, f"{cat} | {link} | Barrio: {barrio_log} | Área: {area_log} | Precio: {precio_log}")

    # FILTRO 4: HABITACIONES (>= 2)
    if num_habs is None or num_habs < MIN_HABITACIONES:
        cat = "Menos de 2 habitaciones"
        return None, (cat, f"{cat} | {link} | Barrio: {barrio_log} | Área: {area_log} | Precio: {precio_log}")

    # APROBADO: cumple los 4 filtros
    parqueadero_txt = extraer_parqueadero(texto_ficha, next_data)
    h1 = page.locator("h1").first
    titulo = h1.inner_text().strip() if h1.count() > 0 else "Apartamento en arriendo"

    cat = "Area permitida (Enviado a Telegram)"
    registro_log = (cat, f"{cat} | {link} | Barrio: {barrio_log} | Área: {area_log} | Precio: {precio_log}")

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
    }, registro_log


def enlace_barrio_relevante(link_limpio):
    """Evita abrir fichas cuyo slug ya indica un barrio ajeno a la zona permitida."""
    m_slug = re.search(r"/apartamento-en-arriendo-en-(.+)-bogota/", link_limpio)
    if not m_slug:
        return True
    slug_barrio = normalizar_texto(m_slug.group(1).replace("-", " "))
    if slug_barrio in LOCALIDADES_GENERICAS_URL:
        return True
    return any(re.search(rf"\b{re.escape(b)}\b", slug_barrio) for b in BARRIOS_PERMITIDOS)


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
            if not enlace_barrio_relevante(link_limpio):
                continue
            prop_id = m_id.group(1)
            if prop_id not in [e[0] for e in enlaces]:
                enlaces.append((prop_id, link_limpio))
        if len(enlaces) >= 50:
            break
    return enlaces


def imprimir_logs_ordenados(registros_log, total_revisados, total_enviados):
    print("=" * 72)
    print(f"Total de apartamentos nuevos revisados: {total_revisados}")
    print(f"Enviados a Telegram: {total_enviados} | Descartados: {total_revisados - total_enviados}")
    print("=" * 72)

    if not registros_log:
        print("No se encontraron apartamentos nuevos para revisar en esta ejecución.")
        return

    registros_ordenados = sorted(registros_log, key=lambda x: (x[0].lower(), x[1].lower()))
    categoria_actual = None
    for categoria, linea in registros_ordenados:
        if categoria != categoria_actual:
            categoria_actual = categoria
            print(f"\n[{categoria_actual}]")
        print(f"- {linea}")


def main():
    vistos = cargar_vistos()
    enlaces_unicos = {}
    registros_log = []
    total_revisados = 0
    total_enviados = 0

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

            total_revisados += 1
            apto, registro_log = procesar_aviso(page, link)
            registros_log.append(registro_log)

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
                total_enviados += 1
                time.sleep(1)

            time.sleep(0.5)

        browser.close()

    guardar_vistos(vistos)
    imprimir_logs_ordenados(registros_log, total_revisados, total_enviados)


if __name__ == "__main__":
    main()