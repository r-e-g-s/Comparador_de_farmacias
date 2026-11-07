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
