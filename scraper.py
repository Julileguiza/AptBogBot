import json
import os
import time
import requests
from playwright.sync_api import sync_playwright

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Pega aquí tus URLs con los filtros ya aplicados y ordenadas por "Más recientes"
URL_FINCARAIZ = "https://www.fincaraiz.com.co/"
URL_METROCUADRADO = "https://www.metrocuadrado.com/"

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
    # Acelera la carga bloqueando imágenes, fuentes y multimedia
    if route.request.resource_type in ["image", "media", "font"]:
        route.abort()
    else:
        route.continue_()


def extraer_fincaraiz(page):
    resultados = []
    page.goto(URL_FINCARAIZ, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)

    # Selecciona las tarjetas de inmuebles
    cards = page.locator("a[href*='/inmueble/']").all()
    for card in cards[:15]:  # Revisa los 15 más recientes
        href = card.get_attribute("href")
        if not href:
            continue
        link = f"https://www.fincaraiz.com.co{href}" if href.startswith("/") else href
        prop_id = f"fr_{link.split('/')[-1]}"
        texto = " | ".join([line.strip() for line in card.inner_text().splitlines() if line.strip()])
        if texto:
            resultados.append({"id": prop_id, "portal": "Fincaraíz", "info": texto[:220], "link": link})
    return resultados


def extraer_metrocuadrado(page):
    resultados = []
    page.goto(URL_METROCUADRADO, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    cards = page.locator("a[href*='/inmueble/']").all()
    for card in cards[:15]:
        href = card.get_attribute("href")
        if not href:
            continue
        link = f"https://www.metrocuadrado.com{href}" if href.startswith("/") else href
        prop_id = f"mc_{link.split('/')[-1]}"
        texto = " | ".join([line.strip() for line in card.inner_text().splitlines() if line.strip()])
        if texto:
            resultados.append({"id": prop_id, "portal": "Metrocuadrado", "info": texto[:220], "link": link})
    return resultados


def main():
    vistos = cargar_vistos()
    nuevos = []

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

        for extractor in [extraer_fincaraiz, extraer_metrocuadrado]:
            try:
                items = extractor(page)
                for item in items:
                    if item["id"] not in vistos:
                        vistos.add(item["id"])
                        nuevos.append(item)
            except Exception as e:
                print(f"Error en extracción: {e}")
            time.sleep(3)

        browser.close()

    for apto in nuevos:
        msg = (
            f"🏠 <b>Nuevo en {apto['portal']}</b>\n"
            f"{apto['info']}\n\n"
            f"🔗 <a href='{apto['link']}'>Ver publicación</a>"
        )
        enviar_telegram(msg)
        time.sleep(1)

    guardar_vistos(vistos)
    print(f"Proceso finalizado. Nuevos encontrados: {len(nuevos)}")


if __name__ == "__main__":
    main()