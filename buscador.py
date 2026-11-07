"""Consultas públicas de catálogos. No elude bloqueos ni garantiza cobertura."""
import json
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

FARMACIAS = {
    'Knop': {'base': 'https://www.farmaciasknop.com', 'search': '/search?q={q}'},
    'Cruz Verde': {'base': 'https://beta.cruzverde.cl', 'search': '/search?query={q}'},
    'Salcobrand': {'base': 'https://salcobrand.cl', 'search': '/search?q={q}'},
    'Ahumada': {'base': 'https://www.farmaciasahumada.cl', 'search': '/search?q={q}'},
    'Dr. Simi': {'base': 'https://www.drsimi.cl', 'search': '/search?q={q}'},
}
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ComparadorPrecios/1.0)', 'Accept-Language': 'es-CL,es;q=0.9'}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def limpiar(s):
    s = unicodedata.normalize('NFKD', str(s).lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', s)).strip()


def precio_clp(valor):
    if isinstance(valor, (int, float)):
        n = int(valor)
        return n if 100 <= n <= 10000000 else None
    if valor is None:
        return None
    s = str(valor).strip().replace('CLP', '').replace('$', '').replace(' ', '')
    if not re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+|\d{3,8}(?:[.,]00)?', s):
        return None
    if re.fullmatch(r'\d+[.,]00', s):
        s = s[:-3]
    n = int(re.sub(r'\D', '', s))
    return n if 100 <= n <= 10000000 else None


def producto(nombre, precio, url, farmacia, marca=''):
    p = precio_clp(precio)
    if not nombre or p is None or not url:
        return None
    base = FARMACIAS[farmacia]['base']
    enlace = urljoin(base, url)
    if not enlace.startswith(('https://', 'http://')) or not enlace.split('/')[2].endswith(base.split('/')[2]):
        return None
    return {'nombre': str(nombre).strip(), 'precio': p, 'url': enlace,
            'farmacia': farmacia, 'marca': str(marca or '').strip()}


def _jsonld(obj, farmacia, origen):
    resultados = []
    if isinstance(obj, list):
        for x in obj:
            resultados.extend(_jsonld(x, farmacia, origen))
    elif isinstance(obj, dict):
        tipo = obj.get('@type', '')
        if isinstance(tipo, list):
            tipo = ' '.join(tipo)
        if 'Product' in str(tipo):
            ofertas = obj.get('offers', {})
            if isinstance(ofertas, list):
                ofertas = next((o for o in ofertas if isinstance(o, dict) and o.get('price')), {})
            if isinstance(ofertas, dict):
                moneda = ofertas.get('priceCurrency', 'CLP')
                if moneda == 'CLP':
                    marca = obj.get('brand', '')
                    if isinstance(marca, dict):
                        marca = marca.get('name', '')
                    x = producto(obj.get('name'), ofertas.get('price'), obj.get('url') or origen, farmacia, marca)
                    if x:
                        resultados.append(x)
        for k in ('@graph', 'itemListElement', 'item'):
            if k in obj:
                resultados.extend(_jsonld(obj[k], farmacia, origen))
    return resultados


def _html_productos(html, farmacia, origen):
    soup = BeautifulSoup(html, 'lxml')
    encontrados = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            encontrados.extend(_jsonld(json.loads(script.string or script.get_text()), farmacia, origen))
        except (ValueError, TypeError):
            continue
    # Extracción conservadora: precio y nombre deben estar en la misma tarjeta.
    cards = soup.select('[data-product-id], [data-product], .product-card, .product-tile, .product-item, .product-grid-item')
    for card in cards[:120]:
        titulo = card.select_one('[itemprop="name"], .product-name, .product-title, .product-card__title, .product-tile__name, h3, h2')
        enlace = card.select_one('a[href]')
        precio = card.select_one('[itemprop="price"], .sale-price, .special-price, .price-sales, .product-price, .price')
        if not (titulo and enlace and precio):
            continue
        valor = precio.get('content') or precio.get('data-price') or precio.get_text(' ', strip=True)
        match = re.search(r'\$\s*([\d.,]+)', valor)
        valor = match.group(1) if match else valor
        x = producto(titulo.get_text(' ', strip=True), valor, enlace.get('href'), farmacia)
        if x:
            encontrados.append(x)
    vistos = set()
    unicos = []
    for p in encontrados:
        key = (limpiar(p['nombre']), p['precio'], p['url'])
        if key not in vistos:
            vistos.add(key)
            unicos.append(p)
    return unicos


def _knop_suggest(texto):
    url = FARMACIAS['Knop']['base'] + '/search/suggest.json'
    r = SESSION.get(url, params={'q': texto, 'resources[type]': 'product', 'resources[limit]': 10}, timeout=14)
    r.raise_for_status()
    data = r.json()
    items = data.get('resources', {}).get('results', {}).get('products', [])
    encontrados = []
    for p in items:
        # Shopify suele devolver precio numérico en CLP; no asumir centavos.
        valor = p.get('price')
        if isinstance(valor, str) and not valor.isdigit():
            valor = re.sub(r'[^\d]', '', valor)
        x = producto(p.get('title'), valor, p.get('url'), 'Knop', p.get('vendor', ''))
        if x:
            encontrados.append(x)
    return encontrados


def buscar(farmacia, texto):
    """Devuelve (productos, estado). Estado explícito ante errores o cero resultados."""
    if farmacia not in FARMACIAS:
        return [], 'Farmacia desconocida'
    if not texto or len(texto.strip()) < 3:
        return [], 'Ingresa al menos 3 caracteres'
    fallos = []
    if farmacia == 'Knop':
        try:
            items = _knop_suggest(texto)
            if items:
                return sorted(items, key=lambda p: puntaje(texto, p['nombre']), reverse=True), 'OK'
        except (requests.RequestException, ValueError, KeyError) as exc:
            fallos.append(type(exc).__name__)
    config = FARMACIAS[farmacia]
    url = config['base'] + config['search'].format(q=quote(texto.strip()))
    try:
        r = SESSION.get(url, timeout=18)
        r.raise_for_status()
        items = _html_productos(r.text, farmacia, r.url)
        items = sorted(items, key=lambda p: puntaje(texto, p['nombre']), reverse=True)
        if items:
            return items[:40], 'OK'
        return [], 'Sin productos extraíbles: puede requerir JavaScript o un endpoint específico'
    except requests.RequestException as exc:
        return [], f'Consulta fallida ({type(exc).__name__}); revisar URL, permisos y conectividad'


def puntaje(a, b):
    x, y = limpiar(a), limpiar(b)
    if not x or not y:
        return 0
    aa, bb = set(x.split()), set(y.split())
    return 0.6 * len(aa & bb) / max(1, len(aa | bb)) + 0.4 * SequenceMatcher(None, x, y).ratio()


def caracteristicas(nombre):
    s = limpiar(nombre).replace(' mcg', ' ug')
    dosis = set(re.findall(r'\b\d+(?:[.,]\d+)?\s*(?:mg|ug|ui|g|ml)\b', s))
    cantidades = set(re.findall(r'\b(?:x\s*)?\d+\s*(?:comprimidos?|capsulas?|tabletas?|gomitas?|sobres?|unidades?|ampollas?)\b', s))
    formas = {f for f in ('jarabe', 'gotas', 'spray', 'efervescente', 'capsulas', 'comprimidos', 'crema', 'gel', 'sachet') if f in s}
    return dosis, cantidades, formas


def equivalencias(base, candidatos):
    """Candidatos orientativos, NO equivalencia médica validada."""
    dosis, cantidad, forma = caracteristicas(base['nombre'])
    ranking = []
    for p in candidatos:
        d, c, f = caracteristicas(p['nombre'])
        # Evitar presentar dosis, formatos o unidades incompatibles como coincidencia.
        if dosis and d and dosis != d:
            continue
        if cantidad and c and cantidad != c:
            continue
        if forma and f and forma != f:
            continue
        score = puntaje(base['nombre'], p['nombre'])
        if score < 0.40:
            continue
        exacto = limpiar(base['nombre']) == limpiar(p['nombre'])
        etiqueta = 'Nombre idéntico' if exacto else 'Posible coincidencia: confirmar composición y presentación'
        ranking.append({**p, 'score': round(score, 3), 'tipo': etiqueta})
    return sorted(ranking, key=lambda p: p['score'], reverse=True)[:5]
