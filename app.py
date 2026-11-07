import streamlit as st
import pandas as pd
from buscador import FARMACIAS, buscar, equivalencias, catalogo_knop, filtrar_catalogo

st.set_page_config(page_title='Comparador de farmacias', page_icon='💊', layout='wide')
st.title('💊 Comparador de farmacias')
st.caption('Knop como referencia · Cruz Verde · Salcobrand · Ahumada · Dr. Simi')
st.warning('Comparador informativo de precios. Las coincidencias sugeridas no acreditan bioequivalencia ni sustitución médica. Confirma producto, dosis, stock, precio final y condiciones en cada farmacia.')

@st.cache_data(ttl=900, show_spinner=False)
def consulta(farmacia, texto):
    return buscar(farmacia, texto)

if 'carro_farmacias' not in st.session_state:
    st.session_state.carro_farmacias = []

@st.cache_data(ttl=21600, show_spinner=False)
def cargar_knop():
    return catalogo_knop()

try:
    with st.spinner('Cargando catálogo de Knop (primera vez puede tardar)...'):
        catalogo = cargar_knop()
    st.caption(f'Catálogo Knop: {len(catalogo)} productos disponibles con precio · actualización cada 6 horas')
    if st.button('Actualizar catálogo Knop'):
        cargar_knop.clear()
        st.rerun()
except Exception as exc:
    catalogo = []
    st.error(f'No fue posible cargar el catálogo completo de Knop: {exc}')

texto = st.text_input('Buscar en el catálogo de Knop', placeholder='Ej.: magnesio quelado')
knop = filtrar_catalogo(catalogo, texto) if texto.strip() else []
if texto.strip() and catalogo:
    if not knop:
        st.info('No se encontraron productos disponibles con esas palabras.')
    else:
        st.success(f'{len(knop)} coincidencias mostradas en Knop')
        idx = st.selectbox('Selecciona el producto exacto de Knop', range(len(knop)),
            format_func=lambda i: f"{knop[i]['nombre']} · ${knop[i]['precio']:,.0f}".replace(',', '.'))
        base = knop[idx]
        c_img, c_info = st.columns([1, 4])
        with c_img:
            if base.get('imagen'):
                st.image(base['imagen'], width=150)
        with c_info:
            st.markdown(f"**Referencia:** [{base['nombre']}]({base['url']}) — ${base['precio']:,.0f}".replace(',', '.'))
            if base.get('precio_dinamico'):
                st.caption('Precio dinámico: confirmar en la web antes de comprar.')
        cantidad = st.number_input('Cantidad', min_value=1, max_value=100, value=1)
        seleccion = {'Knop': base}
        estados = {}
        for farmacia in FARMACIAS:
            if farmacia == 'Knop':
                continue
            with st.spinner(f'Buscando en {farmacia}...'):
                candidatos, estado = consulta(farmacia, base['nombre'])
            estados[farmacia] = estado
            st.subheader(farmacia)
            if not candidatos:
                st.info(f'{estado}. No se asigna precio.')
                continue
            matches = equivalencias(base, candidatos)
            if not matches:
                st.info('Hay resultados, pero ninguno superó los filtros conservadores de coincidencia.')
                continue
            opciones = ['No seleccionar'] + list(range(len(matches)))
            elegido = st.selectbox('Selecciona solo si verificaste que corresponde', opciones,
                format_func=lambda i: 'No seleccionar' if i == 'No seleccionar' else f"{matches[i]['nombre']} · ${matches[i]['precio']:,.0f} · {matches[i]['tipo']}".replace(',', '.'),
                key=f'farm_{farmacia}_{idx}_{texto}')
            if elegido != 'No seleccionar':
                p = matches[elegido]
                st.markdown(f"[Ver producto en {farmacia}]({p['url']})")
                seleccion[farmacia] = p
        st.divider()
        st.subheader('Comparación seleccionada')
        tabla = [{'Farmacia': f, 'Producto': p['nombre'], 'Precio CLP': p['precio'], 'Enlace': p['url']} for f, p in seleccion.items()]
        st.dataframe(pd.DataFrame(tabla), hide_index=True, use_container_width=True, column_config={'Enlace': st.column_config.LinkColumn('Enlace')})
        if st.button('Agregar comparación al carrito', type='primary'):
            st.session_state.carro_farmacias.append({'base': base['nombre'], 'cantidad': cantidad, 'precios': {f: p['precio'] for f, p in seleccion.items()}, 'productos': {f: p['nombre'] for f, p in seleccion.items()}})
            st.success('Comparación agregada')

st.divider()
st.subheader('🛒 Carrito')
carro = st.session_state.carro_farmacias
if carro:
    if st.button('Vaciar carrito'):
        st.session_state.carro_farmacias = []
        st.rerun()
    borrar = None
    filas = []
    for i, item in enumerate(carro):
        c1, c2 = st.columns([9, 1])
        with c1:
            st.write(f"**{item['base']}** · cantidad: {item['cantidad']}")
            st.caption(' | '.join(f"{f}: ${v:,.0f}".replace(',', '.') for f, v in item['precios'].items()))
        with c2:
            if st.button('✕', key=f'borrar_{i}'):
                borrar = i
        for f, v in item['precios'].items():
            filas.append({'Ítem': i + 1, 'Farmacia': f, 'Total CLP': v * item['cantidad']})
    if borrar is not None:
        carro.pop(borrar)
        st.rerun()
    st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
    # Total de farmacia solo si TODOS los productos están cotizados allí.
    totales = {}
    for farmacia in FARMACIAS:
        if all(farmacia in item['precios'] for item in carro):
            totales[farmacia] = sum(item['precios'][farmacia] * item['cantidad'] for item in carro)
    st.subheader('Totales por farmacia (solo canastas completas)')
    if totales:
        for f, total in sorted(totales.items(), key=lambda kv: kv[1]):
            st.metric(f, f'${total:,.0f}'.replace(',', '.'))
    else:
        st.info('No hay una farmacia con precio validado para todos los productos.')
    # Mixto solo para artículos con al menos un precio confirmado (Knop siempre).
    mixto = sum(min(item['precios'].values()) * item['cantidad'] for item in carro)
    st.metric('Compra mixta estimada (sin envío)', f'${mixto:,.0f}'.replace(',', '.'))
    if 'Knop' in totales:
        st.metric('Ahorro frente a comprar todo en Knop', f"${max(0, totales['Knop'] - mixto):,.0f}".replace(',', '.'))
    st.caption('No considera despacho, stock por sucursal, descuentos bancarios, convenios ni restricciones de venta.')
else:
    st.info('Aún no agregas productos.')
