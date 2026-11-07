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
    if valor is None or isinstance(valor,bool):return None
    if isinstance(valor,(int,float)):
        n=int(valor)
        return n if 100 <= n <= 10000000 else None
    texto=str(valor).strip()
    # CLP: $13.999 / 13999; no tomar precios unitarios ni porcentajes
    m=re.search(r'\$?\s*(\d{1,3}(?:\.\d{3})+|\d{3,8})(?!\d)',texto)
    if not m:return None
    n=int(m.group(1).replace('.',''))
    return n if 100 <= n <= 10000000 else None

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

@st.cache_data(ttl=1800,show_spinner=False)
def consultar(farmacia,url):
    """No eludir restricciones: HTTP público, sin cookies ni cabeceras de origen falsas."""
    try:
        u=urlparse(url)
        if u.scheme != 'https' or not u.hostname:return {'precio':None,'estado':'URL no válida'}
        # Lista blanca de dominios esperados; evita consultar hosts arbitrarios
        hosts={
          'Knop':('farmaciasknop.com',), 'Salcobrand':('salcobrand.cl',),
          'Ahumada':('farmaciasahumada.cl',),'Makelawen':('farmaciamapuche.cl',),
          'Dr. Simi':('drsimi.cl',),'Eco Farmacias':('ecofarmacias.cl',),
          'Cruz Verde':('cruzverde.cl',)
        }
        if not any(u.hostname == h or u.hostname.endswith('.'+h) for h in hosts[farmacia]):
            return {'precio':None,'estado':'Dominio no esperado'}
        response=requests.get(url,headers=HEADERS,timeout=14,allow_redirects=True)
        if response.status_code != 200:return {'precio':None,'estado':f'HTTP {response.status_code}'}
        soup=BeautifulSoup(response.text,'html.parser')
        if farmacia=='Salcobrand':
            el=soup.select_one('span.display-price')
            p=precio_entero(el.get_text(' ',strip=True)) if el else None
            return {'precio':p,'estado':'Precio de página' if p else 'Sin precio verificable'}
        if farmacia=='Knop':
            # Shopify: consultar primero el JSON del producto; si falla, usar
            # datos estructurados o metadatos presentes en su página.
            base=url.split('?')[0].rstrip('/')
            try:
                rr=requests.get(base+'.js',headers=HEADERS,timeout=12)
                if rr.ok:
                    data=rr.json()
                    variants=data.get('variants') or []
                    precios=[int(v['price']) for v in variants
                             if isinstance(v.get('price'),(int,float))
                             and v.get('available',True)]
                    if precios:
                        return {'precio':min(precios)//100,'estado':'Precio Shopify'}
            except (requests.RequestException,ValueError,KeyError,TypeError):
                pass
            p=leer_jsonld(soup)
            if p:return {'precio':p,'estado':'Precio de producto'}
            for selector in ('meta[property="product:price:amount"]',
                             'meta[property="og:price:amount"]',
                             'meta[itemprop="price"]'):
                el=soup.select_one(selector)
                if el:
                    p=precio_entero(el.get('content'))
                    if p:return {'precio':p,'estado':'Precio de producto'}
            for selector in ('[data-product-price]', '.price-item--regular',
                             '.price__regular .price-item', '.product__price',
                             '[itemprop="price"]'):
                el=soup.select_one(selector)
                if el:
                    p=precio_entero(el.get('content') or el.get_text(' ',strip=True))
                    if p:return {'precio':p,'estado':'Precio de página'}
        p=leer_jsonld(soup)
        if p:return {'precio':p,'estado':'Precio estructurado (JSON-LD)'}
        return {'precio':None,'estado':'Sin precio verificable'}
    except requests.RequestException:
        return {'precio':None,'estado':'Error de conexión'}

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
    st.info('Importante: las hojas contienen productos idénticos y alternativas similares. No todos los enlaces representan el mismo producto, presentación o dosis. Verifica equivalencia antes de comparar o comprar.')
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
            st.success(f"Menor precio **verificado**: {mejor['Farmacia']} · ${mejor['Precio CLP']:,.0f}".replace(',','.'))
            if len(validos)>1:
                peor=max(validos,key=lambda x:x['Precio CLP'])
                diferencia=peor['Precio CLP']-mejor['Precio CLP']
                col_barato,col_caro=st.columns(2)
                with col_barato:
                    st.metric('Farmacia con menor precio',mejor['Farmacia'],
                              f'${mejor["Precio CLP"]:,}'.replace(',','.'))
                with col_caro:
                    st.metric('Farmacia con mayor precio',peor['Farmacia'],
                              f'${peor["Precio CLP"]:,}'.replace(',','.'))
                st.metric('Ahorro potencial entre el mayor y el menor precio',
                          f'${diferencia:,}'.replace(',','.'),
                          f'{diferencia/peor["Precio CLP"]:.1%} menos respecto del más caro',
                          delta_color='off')
            else:st.caption('Solo una farmacia tiene precio verificado; no es posible afirmar cuál es la más barata entre todas.')
        else:st.warning('No fue posible verificar precios automáticamente para este producto.')
        for x in sorted(datos,key=lambda x:(x['Precio CLP'] is None,x['Precio CLP'] or 0)):
            a,b,c=st.columns([2,2,3]);a.write('**'+x['Farmacia']+'**');b.write(f"${x['Precio CLP']:,}".replace(',','.') if x['Precio CLP'] is not None else 'Precio no verificado');c.link_button('Ver producto',x['URL']);st.caption(x['Estado'])
    else:
        st.write('**Ver productos en farmacias:**')
        for farmacia,url in item['enlaces'].items():st.markdown(f'- [{farmacia}]({url})')

st.divider()
st.caption('Precios orientativos consultados en sitios públicos. Compara presentación, dosis y marca: algunas opciones son alternativas similares, no productos idénticos. El precio final puede variar.')
