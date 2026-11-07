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


def catalogo_knop(max_paginas=100):
    """Carga catálogo público paginado de Knop. Falla explícitamente si está incompleto."""
    base = FARMACIAS['Knop']['base']
    todos = []
    vistos = set()
    total_paginas = None
    for pagina in range(1, max_paginas + 1):
        r = SESSION.get(base + '/products.json', params={'page': pagina, 'per_page': 24}, timeout=25)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get('products'), list):
            raise ValueError('Formato inesperado del catálogo de Knop')
        if int(data.get('page', -1)) != pagina:
            raise ValueError(f'Paginación inesperada: solicitada {pagina}')
        if total_paginas is None:
            total_paginas = int(data.get('total_pages', 0))
            if not 1 <= total_paginas <= max_paginas:
                raise ValueError(f'Cantidad de páginas fuera del límite: {total_paginas}')
        for item in data['products']:
            if not isinstance(item, dict) or item.get('blocked') or not item.get('available', False):
                continue
            vendor = item.get('vendor') or {}
            marca = vendor.get('name', '') if isinstance(vendor, dict) else ''
            x = producto(item.get('name'), item.get('price'), item.get('url'), 'Knop', marca)
            if x and item.get('id') not in vistos:
                x.update({'id': item.get('id'), 'imagen': item.get('image') or '',
                          'precio_regular': precio_clp(item.get('regular_price')),
                          'precio_dinamico': bool(item.get('has_dynamic_price'))})
                vistos.add(item.get('id'))
                todos.append(x)
        if pagina >= total_paginas:
            break
    return todos


def filtrar_catalogo(productos, texto, limite=60):
    tokens = limpiar(texto).split()
    if not tokens:
        return []
    encontrados = [p for p in productos if all(t in limpiar(p['nombre'] + ' ' + p.get('marca', '')) for t in tokens)]
    return sorted(encontrados, key=lambda p: puntaje(texto, p['nombre']), reverse=True)[:limite]


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
    except requests.HTTPError as exc:
        codigo = exc.response.status_code if exc.response is not None else 'desconocido'
        return [], f'Salcobrand HTTP {codigo} · URL: {url}'
    except requests.RequestException as exc:
        return [], f'Error de conexión: {type(exc).__name__}: {str(exc)[:180]}'


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


def buscar_cruzverde(texto, limite=72):
    """Consulta el buscador público de Cruz Verde por término, sin cookies de usuario.

    Devuelve (productos, estado). Los precios y el stock pueden depender de la zona.
    """
    termino = texto.strip()
    if len(termino) < 3:
        return [], 'Ingresa al menos 3 caracteres'
    endpoint = 'https://api.cruzverde.cl/product-service/products/search'
    encontrados = []
    vistos = set()
    total = None
    pagina = 12
    try:
        for offset in range(0, limite, pagina):
            params = {
                'limit': min(pagina, limite - offset),
                'offset': offset,
                'sort': '',
                'q': termino,
                'isAndes': 'true',
                'requestPage': 'SearchPLP',
            }
            r = SESSION.get(endpoint, params=params, timeout=22,
                            headers={'Accept': 'application/json',
                                     'Origin': 'https://www.cruzverde.cl',
                                     'Referer': 'https://www.cruzverde.cl/'})
            print("URL Cruz Verde:", r.url)
            print("Status Cruz Verde:", r.status_code)
            print("Respuesta Cruz Verde:", r.text[:500])
            if r.status_code != 200:
                raise RuntimeError(
                    f"Cruz Verde HTTP {r.status_code}: "
                    f"{r.text[:300]}"
                )
            data = r.json()
            if not isinstance(data, dict) or not isinstance(data.get('hits'), list):
                raise ValueError('Respuesta inesperada de Cruz Verde')
            if total is None:
                total = data.get('total')
            hits = data['hits']
            if not hits:
                break
            for item in hits:
                if not isinstance(item, dict) or item.get('hitType') != 'product':
                    continue
                sku = str(item.get('productId') or '')
                if not sku or sku in vistos:
                    continue
                precios = item.get('prices') or {}
                normal = precio_clp(precios.get('price-list-cl'))
                oferta = precio_clp(precios.get('price-sale-cl'))
                actual = oferta if oferta is not None else normal
                if actual is None:
                    continue
                imagen = item.get('image') or {}
                # El link de la API apunta a beta/demandware, no a una ficha comercial.
                # Enlazamos al buscador oficial hasta validar el formato de fichas.
                enlace = 'https://www.cruzverde.cl/search?q=' + quote(termino)
                encontrados.append({
                    'id': sku, 'nombre': str(item.get('productName') or '').strip(),
                    'farmacia': 'Cruz Verde', 'marca': item.get('brand') or '',
                    'precio': actual, 'precio_regular': normal,
                    'precio_oferta': oferta, 'url': enlace,
                    'imagen': imagen.get('disBaseLink') or imagen.get('link') or '',
                    'stock': item.get('stock'),
                })
                vistos.add(sku)
            if len(hits) < params['limit'] or (isinstance(total, int) and offset + len(hits) >= total):
                break
        if encontrados:
            estado = 'OK'
            if isinstance(total, int) and total > len(encontrados):
                estado = f'OK · {len(encontrados)} de {total} resultados (límite {limite})'
            return encontrados, estado
        return [], 'Sin resultados con precio para esta búsqueda'
    except (requests.RequestException, ValueError, TypeError, RuntimeError) as exc:
        print('ERROR Cruz Verde:', type(exc).__name__, str(exc)[:300])
        if encontrados:
            return encontrados, f'Resultados parciales: {type(exc).__name__}'
        return [], f'No se pudo consultar Cruz Verde ({type(exc).__name__})'
