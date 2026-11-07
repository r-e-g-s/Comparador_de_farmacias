import json, re, time
from pathlib import Path
from urllib.parse import urlparse
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title='Comparador de precios de farmacias', page_icon='💊', layout='wide')
CATALOGO = json.loads(Path(__file__).with_name('catalogo_comparador.json').read_text(encoding='utf-8'))
FARMACIAS = ['Knop','Salcobrand','Ahumada','Makelawen','Dr. Simi','Eco Farmacias','Cruz Verde']
HEADERS = {'User-Agent':'Mozilla/5.0 (compatible; ComparadorPrecios/0.1)'}

def precio_entero(valor):
    """CLP: no interpretar el final de $6.200 como $200."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        n = int(valor)
        return n if 1000 <= n <= 10000000 else None
    texto = str(valor).strip()
    # VTEX puede separar el precio en spans: '$ 6 . 200'.
    texto = re.sub(r'(?<=\d)\s*\.\s*(?=\d{3}(?!\d))', '.', texto)
    # Aceptar $6.200, $6200 o 6200, pero no sufijos parciales.
    m = re.search(r'(?<![\d.,])(?:\$\s*)?(\d{1,3}(?:\.\d{3})+|\d{4,8})(?![\d.,])', texto)
    if not m:
        return None
    n = int(m.group(1).replace('.', ''))
    return n if 1000 <= n <= 10000000 else None


def leer_jsonld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):continue
        stack=data if isinstance(data,list) else [data]
        while stack:
            obj=stack.pop()
            if not isinstance(obj,dict):continue
            if isinstance(obj.get('@graph'),list):stack.extend(obj['@graph'])
            tipo=obj.get('@type',[])
            if isinstance(tipo,str):tipo=[tipo]
            if 'Product' not in tipo:continue
            offers=obj.get('offers',{})
            if isinstance(offers,list):offers=offers[0] if offers else {}
            if isinstance(offers,dict):
                currency=offers.get('priceCurrency')
                if currency and currency != 'CLP':continue
                p=precio_entero(offers.get('price'))
                if p:return p
    return None

def precio_visible(soup, farmacia):
    """Lee únicamente precios del bloque principal, no precios unitarios o de sugeridos."""
    selectores = {
        'Knop': ['[data-testid="product-price"]', '[data-product-price]',
                 '.product-price .price', '.product__price .price',
                 '.product-price', '.product__price', '.price--sale'],
        'Makelawen': ['.product-info-main .price-wrapper[data-price-amount]',
                      '.product-info-main .price-final_price .price',
                      '.product-info-main .price-wrapper .price',
                      '.product-info-main .price'],
        'Dr. Simi': ['[data-testid="selling-price"]',
                     '.vtex-product-price-1-x-sellingPriceValue',
                     '.vtex-product-price-1-x-sellingPrice',
                     '[class*="sellingPriceValue"]'],
        'Cruz Verde': ['[data-testid="selling-price"]',
                       '.product-price .price', '.price-sales'],
    }
    for selector in selectores.get(farmacia, []):
        for el in soup.select(selector)[:3]:
            raw = el.get('data-price-amount') or el.get('content') or el.get_text(' ', strip=True)
            precio = precio_entero(raw)
            if precio is not None:
                return precio
    return None


def precio_knop_texto(soup):
    # El sitio de Knop actual no es Shopify. Usamos el precio mostrado cerca
    # del título del producto, evitando el precio por cápsula y recomendados.
    h = soup.find('h1')
    if not h:
        return None
    texto = soup.get_text(' ', strip=True)
    titulo = h.get_text(' ', strip=True)
    pos = texto.find(titulo)
    if pos < 0:
        return None
    zona = texto[pos + len(titulo):pos + len(titulo) + 180]
    precios = re.findall(r'\$\s*(\d{1,3}(?:\.\d{3})+|\d{3,8})\b', zona)
    if not precios:
        return None
    return precio_entero(precios[0])


def consultar_cruzverde(url):
    """Consulta OCAPI pública; no utiliza credenciales privadas ni sesiones."""
    path = urlparse(url).path
    match = re.search(r"/(\d+)\.html$", path)
    if not match:
        return {'precio': None, 'estado': 'No se encontró el código del producto'}
    codigo = match.group(1)
    endpoint = f'https://beta.cruzverde.cl/s/Chile/dw/shop/v19_1/products/{codigo}'
    params = {
        'client_id': 'c19ce24d-1677-4754-b9f7-c193997c5a92',
        'expand': 'prices,promotions',
    }
    try:
        r = requests.get(endpoint, params=params,
                         headers={'Accept': 'application/json', 'User-Agent': HEADERS['User-Agent']},
                         timeout=18)
        if not r.ok:
            return {'precio': None, 'estado': f'API Cruz Verde: HTTP {r.status_code}'}
        data = r.json()
        if str(data.get('id')) != codigo:
            return {'precio': None, 'estado': 'El producto consultado no coincide con el enlace'}
        precios = data.get('prices') or {}
        candidatos = []
        for campo, etiqueta in [('price', 'Precio de lista')]:
            p = precio_entero(data.get(campo))
            if p is not None:
                candidatos.append((p, etiqueta, False))
        p = precio_entero(precios.get('price-sale-cl'))
        if p is not None:
            candidatos.append((p, 'Precio de oferta API', False))
        for promo in data.get('product_promotions') or []:
            if not isinstance(promo, dict):
                continue
            p = precio_entero(promo.get('promotional_price'))
            if p is not None:
                nombre = str(promo.get('promotion_id') or 'promoción publicada')
                candidatos.append((p, f'Promoción publicada: {nombre}', True))
        if not candidatos:
            return {'precio': None, 'estado': 'API sin precios verificables'}
        p, origen, es_promo = min(candidatos, key=lambda x: x[0])
        estado = origen + (' · Verificar condiciones de la promoción en tienda' if es_promo else '')
        return {'precio': p, 'estado': estado}
    except (requests.RequestException, ValueError, TypeError):
        return {'precio': None, 'estado': 'No se pudo consultar la API de Cruz Verde'}


@st.cache_data(ttl=1800, show_spinner=False)
def consultar(farmacia, url):
    """Consulta páginas públicas, sin credenciales ni eludir controles de acceso."""
    hosts = {
        'Knop': ('farmaciasknop.com',),
        'Salcobrand': ('salcobrand.cl',),
        'Ahumada': ('farmaciasahumada.cl',),
        'Makelawen': ('farmaciamapuche.cl',),
        'Dr. Simi': ('drsimi.cl',),
        'Eco Farmacias': ('ecofarmacias.cl',),
        'Cruz Verde': ('cruzverde.cl',),
    }
    try:
        u = urlparse(url)
        if u.scheme != 'https' or not u.hostname or farmacia not in hosts:
            return {'precio': None, 'estado': 'Enlace no válido'}
        if not any(u.hostname == h or u.hostname.endswith('.' + h) for h in hosts[farmacia]):
            return {'precio': None, 'estado': 'Dominio no esperado'}
        if farmacia == 'Cruz Verde':
            return consultar_cruzverde(url)
        response = requests.get(url, headers=HEADERS, timeout=18)
        if response.status_code != 200:
            return {'precio': None, 'estado': f'No se pudo consultar (HTTP {response.status_code})'}
        soup = BeautifulSoup(response.text, 'html.parser')

        if farmacia == 'Knop':
            p = precio_visible(soup, farmacia) or precio_knop_texto(soup)
            # No usar JSON-LD como respaldo para Knop: devolvía $990
            # mientras la página mostraba $8.990.
            return {'precio': p, 'estado': 'Precio de página' if p else 'Precio no verificable'}

        if farmacia == 'Salcobrand':
            el = soup.select_one('span.display-price')
            p = precio_entero(el.get_text(' ', strip=True)) if el else None
            return {'precio': p, 'estado': 'Precio de página' if p else 'Precio no verificable'}

        if farmacia == 'Dr. Simi':
            # Priorizar el precio de venta visible del producto principal.
            p = precio_visible(soup, farmacia)
            if p:
                return {'precio': p, 'estado': 'Precio de venta publicado'}
            p = leer_jsonld(soup)
            if p:
                return {'precio': p, 'estado': 'Precio publicado'}
        p = precio_visible(soup, farmacia)
        if p:
            return {'precio': p, 'estado': 'Precio de página'}
        p = leer_jsonld(soup)
        if p:
            return {'precio': p, 'estado': 'Precio publicado'}
        return {'precio': None, 'estado': 'Precio no verificable automáticamente (puede cargarse con JavaScript)'}
    except (requests.RequestException, ValueError, TypeError):
        return {'precio': None, 'estado': 'No se pudo consultar el precio'}

st.title('💊 Comparador de precios de farmacias')
st.caption('Compara precios de productos y alternativas en farmacias de Chile. Los valores pueden variar según comuna, promociones y disponibilidad.')
col1,col2=st.columns([3,1])
with col1: buscar=st.text_input('Buscar producto',placeholder='Ej.: magnesio, omega, probiótico')
with col2: farmacia_filtro=st.selectbox('Farmacia', ['Todas']+FARMACIAS)
filtrados=[p for p in CATALOGO if (not buscar or buscar.casefold() in p['nombre'].casefold()) and (farmacia_filtro=='Todas' or farmacia_filtro in p['enlaces'])]
st.write(f'**{len(filtrados)} productos encontrados** · {len(CATALOGO)} productos en el catálogo')
if not filtrados:st.info('No hay productos que coincidan con los filtros.')
else:
    nombres=[f"{p['nombre']} · {len(p['enlaces'])} farmacias" for p in filtrados]
    indice=st.selectbox('Selecciona un producto',range(len(filtrados)),format_func=lambda i:nombres[i])
    item=filtrados[indice]
    st.subheader(item['nombre'])
    st.info('Las alternativas pueden tener distintas marcas, cantidades y formulaciones. Compara la presentación antes de decidir.')
    if st.button('Consultar precios de este producto',type='primary'):
        with st.spinner('Consultando las páginas públicas disponibles...'):
            datos=[]
            for farmacia,url in item['enlaces'].items():
                resultado=consultar(farmacia,url)
                datos.append({'Farmacia':farmacia,'Precio CLP':resultado['precio'],'Estado':resultado['estado'],'URL':url})
                time.sleep(0.2)
            st.session_state['consulta']={'id':item['nombre'],'datos':datos}
    consulta=st.session_state.get('consulta')
    if consulta and consulta['id']==item['nombre']:
        datos=consulta['datos']
        validos=[x for x in datos if x['Precio CLP'] is not None]
        if validos:
            mejor=min(validos,key=lambda x:x['Precio CLP'])
            st.success(f"Menor precio **publicado**: {mejor['Farmacia']} · ${mejor['Precio CLP']:,.0f}".replace(',','.'))
            if len(validos)>1:
                peor=max(validos,key=lambda x:x['Precio CLP'])
                diferencia=peor['Precio CLP']-mejor['Precio CLP']
                col_barato,col_caro=st.columns(2)
                with col_barato:
                    st.metric(label='Farmacia con menor precio', value=mejor['Farmacia'])
                    st.caption(f'Precio: ${mejor["Precio CLP"]:,}'.replace(',', '.'))
                with col_caro:
                    st.metric(label='Farmacia con mayor precio', value=peor['Farmacia'])
                    st.caption(f'Precio: ${peor["Precio CLP"]:,}'.replace(',', '.'))
                st.metric(
                    label='Diferencia entre alternativas consultadas',
                    value=f'${diferencia:,}'.replace(',', '.')
                )
                st.caption(f'Ahorro potencial: {diferencia/peor["Precio CLP"]:.1%} respecto del precio mayor, si las alternativas son comparables.')
            else:st.caption('Solo una farmacia tiene precio disponible; no es posible afirmar cuál es la más barata entre todas.')
        else:st.warning('No fue posible verificar precios automáticamente para este producto.')
        for x in sorted(datos,key=lambda x:(x['Precio CLP'] is None,x['Precio CLP'] or 0)):
            a,b,c=st.columns([2,2,3]);a.write('**'+x['Farmacia']+'**');b.write(f"${x['Precio CLP']:,}".replace(',','.') if x['Precio CLP'] is not None else 'Precio no verificado');c.link_button('Ver producto',x['URL']);st.caption(x['Estado'])
    else:
        st.write('**Ver productos en farmacias:**')
        for farmacia,url in item['enlaces'].items():st.markdown(f'- [{farmacia}]({url})')

st.divider()
st.caption('Precios orientativos obtenidos de páginas públicas y servicios públicos de producto. Las promociones pueden requerir condiciones especiales. Verifica precio final, presentación, disponibilidad y despacho en la farmacia antes de comprar.')
