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
