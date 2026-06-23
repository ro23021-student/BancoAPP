"""
app.py — Sistema Bancario con Streamlit
Arquitectura:
  models.py        → SQLAlchemy ORM (clientes, movimientos, préstamos, cuentas contables)
  contabilidad.py  → Motor de partida doble propio
  operaciones.py   → Lógica de negocio
  app.py           → UI Streamlit
"""

import streamlit as st
import random
from decimal import Decimal
import pandas as pd
import plotly.express as px
from datetime import datetime
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, selectinload
from contabilidad import registrar

from models import (
    BaseLocal, Cliente, Movimiento, Prestamo,
    CuentaContable, Asiento, LineaAsiento, ConfigBanco,
    Usuario, AuditLog,
    TipoCuenta, Beneficiario, DepositoPlazoFijo, Garantia,
    ScoreCredito, TarjetaDebito, TarjetaCredito,
    Sucursal, ATM, AlertaAML, CierreDiario,
    Socio, AporteSocio, CuotaPrestamo,
)
from extras import (
    inicializar_tipos_cuenta, inicializar_sucursales,
    agregar_beneficiario, eliminar_beneficiario,
    crear_deposito_plazo, vencer_deposito_plazo,
    agregar_garantia, refinanciar_prestamo,
    calcular_score, emitir_tarjeta_debito, emitir_tarjeta_credito,
    verificar_aml, obtener_alertas_aml, revisar_alerta_aml,
    realizar_cierre_diario, generar_balance_general, generar_estado_resultados,
    registrar_socio, registrar_aporte, historial_cliente,
    actualizar_cuotas_vencidas,
)
from contabilidad import (
    inicializar_contabilidad, saldo_cuenta, caja_real,
    reconciliar, PLAN_CUENTAS,
)
from operaciones import (
    crear_cliente, depositar, retirar, transferir,
    otorgar_prestamo, pagar_prestamo, devengar_interes,
    editar_cliente, suspender_cliente, reactivar_cliente, cerrar_cuenta,
    revertir_operacion, verificar_limite_diario,
    OPERACIONES_REVERSIBLES, VENTANA_REVERSION_MIN,
    # Nuevas operaciones — plan de cuentas ampliado
    registrar_inversion, liquidar_inversion,
    adquirir_bien_inmueble,
    clasificar_prestamo_moroso, constituir_provision, castigar_prestamo_incobrable,
    deposito_cuenta_ahorro, deposito_cuenta_corriente,
    recibir_prestamo_banco, pagar_obligacion_banco,
    provisionar_impuesto, pagar_impuesto,
    constituir_reserva_legal, registrar_utilidad_ejercicio,
    registrar_comision_tarjeta_credito,
    cobrar_mora_prestamo,
    registrar_gasto_operativo,
    registrar_mora_pagada_banco,
)
from auth import (
    hay_usuarios, registrar_primer_admin, login,
    crear_usuario, cambiar_password, toggle_usuario, cambiar_rol,
    registrar_log, tiene_permiso, MENU_POR_ROL, ROL_COLOR,
)
# alias: audit() calls in vista_operaciones deben registrar en el log
audit = registrar_log
from exportar import (
    generar_estado_cuenta_pdf, generar_comprobante_pdf,
    generar_amortizacion_pdf, generar_movimientos_csv,
    generar_balance_csv, generar_balance_pdf,
)
from alertas import (
    obtener_todas_alertas, contar_alertas, calcular_mora,
    NIVEL_ERROR, NIVEL_WARNING, NIVEL_INFO,
)
from operaciones import tasa_deposito, tasa_transferencia, tasa_prestamo

# ─────────────────────────────
# ENGINE  (una sola instancia)
# ─────────────────────────────
@st.cache_resource
def get_engine():
    db_url = st.secrets.get("DATABASE_URL", "sqlite:///banco.db")
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True, pool_size=5, max_overflow=10)
    engine = create_engine(db_url, connect_args=connect_args)
    BaseLocal.metadata.create_all(engine)
    return engine


# ─────────────────────────────
# SESIÓN  (por request)
# ─────────────────────────────
def get_session():
    return Session(get_engine())


# ─────────────────────────────
# HELPERS UI
# ─────────────────────────────
COLORES = ["#A89CC8", "#7EC8C8", "#C8A87E", "#C87E9C", "#7EA8C8", "#98C87E", "#C8C87E"]

BADGE_MAP = {
    "Deposito":               ("badge-green",  "Depósito"),
    "Retiro":                 ("badge-red",    "Retiro"),
    "Prestamo":               ("badge-blue",   "Préstamo"),
    "Pago Prestamo":          ("badge-purple", "Pago Préstamo"),
    "Transferencia Enviada":  ("badge-amber",  "Transferencia →"),
    "Transferencia Recibida": ("badge-green",  "← Transferencia"),
    "Apertura":               ("badge-gray",   "Apertura"),
}

def badge(tipo):
    cls, label = BADGE_MAP.get(tipo, ("badge-gray", tipo))
    return f'<span class="badge {cls}">{label}</span>'

def monto_cls(tipo):
    positivos = {"Deposito", "Transferencia Recibida", "Apertura", "Prestamo"}
    negativos = {"Retiro", "Transferencia Enviada", "Pago Prestamo"}
    if tipo in positivos: return "monto-pos"
    if tipo in negativos: return "monto-neg"
    return "monto-neu"

def render_header(icon, titulo, subtitulo=""):
    sub = f"<p style='margin:0;font-size:.8rem;color:#64748B'>{subtitulo}</p>" if subtitulo else ""
    st.markdown(
        f"<div class='section-header'>"
        f"<h1 style='margin:0;font-size:1.25rem;font-weight:600'>{icon} {titulo}</h1>{sub}"
        f"</div>",
        unsafe_allow_html=True,
    )

def render_metric(label, value, delta=None, delta_type="neutral"):
    delta_html = ""
    if delta:
        cls   = "metric-delta-up" if delta_type == "up" else "metric-delta-down"
        arrow = "▲" if delta_type == "up" else "▼"
        delta_html = f"<div class='{cls}'>{arrow} {delta}</div>"
    st.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>{label}</div>"
        f"<div class='metric-value'>{value}</div>"
        f"{delta_html}"
        f"</div>",
        unsafe_allow_html=True,
    )

def render_movimientos(movimientos):
    if not movimientos:
        st.markdown('<div class="alert-info">No hay movimientos registrados.</div>',
                    unsafe_allow_html=True)
        return
    filas = "".join(
        f"<tr>"
        f"<td style='color:#64748B;font-size:.8rem'>"
        f"{m.fecha.strftime('%Y-%m-%d %H:%M') if hasattr(m.fecha,'strftime') else m.fecha}"
        f"</td>"
        f"<td>{badge(m.tipo)}</td>"
        f"<td class='{monto_cls(m.tipo)}'>${m.monto:,.2f}</td>"
        f"<td style='color:#64748B;font-size:.82rem'>{m.descripcion}</td>"
        f"</tr>"
        for m in movimientos
    )
    st.markdown(
        f"<table class='mov-table'>"
        f"<thead><tr><th>Fecha</th><th>Tipo</th><th>Monto</th><th>Descripción</th></tr></thead>"
        f"<tbody>{filas}</tbody></table>",
        unsafe_allow_html=True,
    )

def alert(tipo, msg):
    st.markdown(f'<div class="alert-{tipo}">{msg}</div>', unsafe_allow_html=True)


# ─────────────────────────────
# ESTILOS
# ─────────────────────────────
def inject_styles():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

/* ── Base ── */
html, body, [class*="css"] {
  font-family: 'Inter', sans-serif;
  background-color: #1A1720;
  color: #E8E4D9;
}
.stApp { background-color: #1A1720 !important; }
[data-testid="stHeader"] { background: #1A1720 !important; }
[data-testid="stToolbar"] { background: #1A1720 !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
  background: #231F2B !important;
  border-right: 1px solid #3D3B3C !important;
}
[data-testid="stSidebar"] * { color: #B8B4C8 !important; }
[data-testid="stSidebar"] hr { border-color: #3D3B3C !important; }

/* ── Encabezados ── */
.section-header {
  border-left: 4px solid #5C5464;
  padding: 7px 16px;
  margin-bottom: 1.5rem;
  background: linear-gradient(90deg, rgba(92,84,100,.25) 0%, transparent 100%);
  border-radius: 0 10px 10px 0;
}
.section-header h1 { color: #E8E4D9 !important; font-weight: 600 !important; }

/* ── Tarjetas métricas ── */
.metric-card {
  border: 1px solid #3D3B3C;
  border-radius: 14px;
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  background: #2A2733;
}
.metric-label {
  font-size: .71rem;
  font-weight: 600;
  color: #8A8694;
  text-transform: uppercase;
  letter-spacing: .08em;
}
.metric-value {
  font-size: 1.55rem;
  font-weight: 700;
  font-family: 'DM Mono', monospace;
  color: #E8E4D9;
}
.metric-delta-up   { font-size: .75rem; color: #7EC8A8; font-weight: 600; }
.metric-delta-down { font-size: .75rem; color: #C87E7E; font-weight: 600; }

/* ── Badges ── */
.badge { display:inline-block; padding:3px 12px; border-radius:20px; font-size:.72rem; font-weight:600; }
.badge-green  { background:rgba(126,200,168,.2);  color:#7EC8A8; }
.badge-blue   { background:rgba(126,168,200,.2);  color:#7EA8C8; }
.badge-amber  { background:rgba(200,168,126,.2);  color:#C8A87E; }
.badge-red    { background:rgba(200,126,126,.2);  color:#C87E7E; }
.badge-purple { background:rgba(168,156,200,.2);  color:#A89CC8; }
.badge-gray   { background:rgba(92,84,100,.3);    color:#B8B4C8; }

/* ── Tabla movimientos ── */
.mov-table { width:100%; border-collapse:collapse; font-size:.85rem; }
.mov-table th {
  text-align:left; color:#8A8694; font-weight:600;
  padding:9px 13px; border-bottom:2px solid #3D3B3C;
  font-size:.71rem; text-transform:uppercase; letter-spacing:.06em;
}
.mov-table td {
  padding:10px 13px;
  border-bottom:1px solid #2E2B38;
  vertical-align:middle;
  color:#C8C4D8;
}
.mov-table tr:hover td { background: rgba(92,84,100,.2); }

/* ── Montos ── */
.monto-pos { color:#7EC8A8; font-weight:700; font-family:'DM Mono',monospace; }
.monto-neg { color:#C87E7E; font-weight:700; font-family:'DM Mono',monospace; }
.monto-neu { color:#A89CC8; font-weight:700; font-family:'DM Mono',monospace; }

/* ── Alertas ── */
.alert-success {
  background:rgba(126,200,168,.12); color:#7EC8A8;
  padding:11px 16px; border-radius:10px;
  border-left:4px solid #7EC8A8; font-size:.875rem; margin:8px 0;
}
.alert-error {
  background:rgba(200,126,126,.12); color:#C87E7E;
  padding:11px 16px; border-radius:10px;
  border-left:4px solid #C87E7E; font-size:.875rem; margin:8px 0;
}
.alert-info {
  background:rgba(92,84,100,.3); color:#B8B4C8;
  padding:11px 16px; border-radius:10px;
  border-left:4px solid #5C5464; font-size:.875rem; margin:8px 0;
}
.alert-warning {
  background:rgba(200,168,126,.12); color:#C8A87E;
  padding:11px 16px; border-radius:10px;
  border-left:4px solid #C8A87E; font-size:.875rem; margin:8px 0;
}

/* ── Saldo box ── */
.saldo-box {
  display:flex; align-items:center; gap:12px; padding:14px 18px;
  border-radius:12px; margin-bottom:14px;
  background:#2A2733;
  border:1px solid #3D3B3C;
}

/* ── Botones ── */
.stButton > button {
  border-radius:10px !important;
  font-weight:600 !important;
  font-family:'Inter',sans-serif !important;
  transition: all .18s ease !important;
  border: 1px solid #3D3B3C !important;
  background: #2A2733 !important;
  color: #E8E4D9 !important;
}
.stButton > button[kind="primary"] {
  background: #5C5464 !important;
  border: 1px solid #6E6578 !important;
  color: #E8E4D9 !important;
}
.stButton > button[kind="primary"]:hover {
  background: #6E6578 !important;
  color: #ffffff !important;
}

/* ── Tabs ── */
.stTabs [role="tab"]              { color: #8A8694 !important; font-weight:500 !important; }
.stTabs [aria-selected="true"]   { color: #E8E4D9 !important; border-bottom:3px solid #5C5464 !important; font-weight:700 !important; }

/* ── Inputs ── */
.stTextInput input,
.stNumberInput input {
  background: #2A2733 !important;
  border: 1px solid #3D3B3C !important;
  border-radius: 8px !important;
  color: #E8E4D9 !important;
}
div[data-baseweb="select"] > div {
  background: #2A2733 !important;
  border: 1px solid #3D3B3C !important;
  border-radius: 8px !important;
  color: #E8E4D9 !important;
}

/* ── Dividers ── */
hr { border-color: #3D3B3C !important; }

/* ── DataFrames ── */
[data-testid="stDataFrame"] { background: #2A2733 !important; }
</style>""", unsafe_allow_html=True)


# ─────────────────────────────
# VISTAS
# ─────────────────────────────

def vista_panel(session):
    render_header(
        "📊", "Panel principal",
        f"Resumen operativo · {datetime.now().strftime('%d/%m/%Y %H:%M')}",
    )

    clientes    = session.query(Cliente).all()
    saldo_total = sum((c.saldo or Decimal("0")) for c in clientes)
    caja        = caja_real(session)
    comisiones  = saldo_cuenta(session, "Ingresos Comisiones")
    intereses   = saldo_cuenta(session, "Ingresos Intereses")
    utilidad    = comisiones + intereses
    num_prest   = session.query(Prestamo).filter_by(estado="ACTIVO").count()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: render_metric("Clientes",          str(len(clientes)))
    with col2: render_metric("Saldo clientes",    f"${saldo_total:,.2f}")
    with col3: render_metric("Caja real",         f"${caja:,.2f}")
    with col4: render_metric("Préstamos activos", str(num_prest))
    with col5: render_metric("Utilidad acumulada", f"${utilidad:,.2f}",
                             delta=f"${comisiones:,.2f} comisiones", delta_type="up")
    st.divider()

    if clientes:
        col1, col2 = st.columns([3, 2])
        with col1:
            top = sorted(clientes, key=lambda x: x.saldo, reverse=True)[:7]
            fig = px.bar(
                x=[c.nombre for c in top], y=[float(c.saldo) for c in top],
                labels={"x": "", "y": "Saldo ($)"},
                color_discrete_sequence=[COLORES[0]],
            )
            fig.update_layout(
                title=dict(text="Top clientes por saldo", font=dict(size=14)),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=36, b=0), height=260,
                yaxis=dict(tickprefix="$"), showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            tipos = {}
            for c in clientes:
                tipos[c.tipo] = tipos.get(c.tipo, 0) + 1
            fig2 = px.pie(
                values=list(tipos.values()), names=list(tipos.keys()),
                color_discrete_sequence=COLORES, hole=0.55,
            )
            fig2.update_layout(
                title=dict(text="Tipos de cuenta", font=dict(size=14)),
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=36, b=0), height=260,
            )
            st.plotly_chart(fig2, use_container_width=True)

    # ── Fila 2: alertas AML, score, sucursales ──
    st.divider()
    col_aml, col_score, col_suc, col_dpf = st.columns(4)

    aml_pendientes = session.query(func.count(AlertaAML.id)).filter_by(estado="PENDIENTE").scalar() or 0
    with col_aml:
        color_aml = "#e74c3c" if aml_pendientes > 0 else "#2ecc71"
        st.markdown(f"<div style='background:#1e1e2e;border:1px solid #3d3b3c;border-radius:8px;padding:12px;text-align:center'>"
                    f"<div style='color:#8a8694;font-size:.75rem'>Alertas AML</div>"
                    f"<div style='font-size:1.6rem;font-weight:700;color:{color_aml}'>{aml_pendientes}</div>"
                    f"<div style='color:#8a8694;font-size:.72rem'>pendientes</div></div>", unsafe_allow_html=True)

    score_prom = session.query(func.avg(Cliente.score_credito)).filter(Cliente.estado=="ACTIVO").scalar()
    score_prom = round(float(score_prom), 0) if score_prom else 500
    score_c = "#2ecc71" if score_prom >= 650 else "#f39c12" if score_prom >= 500 else "#e74c3c"
    with col_score:
        st.markdown(f"<div style='background:#1e1e2e;border:1px solid #3d3b3c;border-radius:8px;padding:12px;text-align:center'>"
                    f"<div style='color:#8a8694;font-size:.75rem'>Score promedio</div>"
                    f"<div style='font-size:1.6rem;font-weight:700;color:{score_c}'>{int(score_prom)}</div>"
                    f"<div style='color:#8a8694;font-size:.72rem'>cartera</div></div>", unsafe_allow_html=True)

    num_sucursales = session.query(func.count(Sucursal.id)).filter_by(activa=True).scalar() or 0
    with col_suc:
        st.markdown(f"<div style='background:#1e1e2e;border:1px solid #3d3b3c;border-radius:8px;padding:12px;text-align:center'>"
                    f"<div style='color:#8a8694;font-size:.75rem'>Sucursales activas</div>"
                    f"<div style='font-size:1.6rem;font-weight:700;color:#7ec8c8'>{num_sucursales}</div>"
                    f"<div style='color:#8a8694;font-size:.72rem'>en operación</div></div>", unsafe_allow_html=True)

    num_dpf = session.query(func.count(DepositoPlazoFijo.id)).filter_by(estado="ACTIVO").scalar() or 0
    with col_dpf:
        st.markdown(f"<div style='background:#1e1e2e;border:1px solid #3d3b3c;border-radius:8px;padding:12px;text-align:center'>"
                    f"<div style='color:#8a8694;font-size:.75rem'>Plazos fijos activos</div>"
                    f"<div style='font-size:1.6rem;font-weight:700;color:#a89cc8'>{num_dpf}</div>"
                    f"<div style='color:#8a8694;font-size:.72rem'>certificados</div></div>", unsafe_allow_html=True)

    st.divider()
    st.markdown("#### Últimos 10 movimientos")
    ultimos = (
        session.query(Movimiento)
        .order_by(Movimiento.fecha.desc())
        .limit(10).all()
    )
    render_movimientos(ultimos)


def vista_clientes(session, usuario):
    render_header("👥", "Gestión de clientes")

    puede_crear  = tiene_permiso(usuario, "crear_cliente")
    puede_editar = tiene_permiso(usuario, "editar_cliente")
    puede_estado = tiene_permiso(usuario, "gestionar_estado_cliente")

    # Construir tabs dinámicamente según permisos
    tab_labels = ["📋 Lista"]
    if puede_crear:  tab_labels.append("➕ Nuevo cliente")
    if puede_editar: tab_labels.append("✏️ Editar")
    if puede_estado: tab_labels.append("🔒 Estado cuenta")
    tab_labels.append("📜 Historial")

    tabs = st.tabs(tab_labels)
    tab_map = {label: tab for label, tab in zip(tab_labels, tabs)}

    # ── Tab: Lista ────────────────────────────────────────────
    with tab_map["📋 Lista"]:
        col_filt, _ = st.columns([2, 3])
        with col_filt:
            filtro_estado = st.selectbox(
                "Filtrar por estado", ["Todos", "ACTIVO", "SUSPENDIDO", "CERRADO"],
                key="filt_estado"
            )
        q = session.query(Cliente).order_by(Cliente.nombre)
        if filtro_estado != "Todos":
            q = q.filter(Cliente.estado == filtro_estado)
        clientes = q.all()

        ESTADO_ICON = {"ACTIVO": "🟢", "SUSPENDIDO": "🟡", "CERRADO": "🔴"}
        if clientes:
            df = pd.DataFrame([{
                "Nº Cuenta":   c.num_cuenta or "—",
                "Nombre":      c.nombre,
                "Tipo":        c.tipo.capitalize(),
                "Estado":      ESTADO_ICON.get(c.estado, "❓") + " " + (c.estado or "—"),
                "Documento":   (c.tipo_documento or "") + " " + (c.documento or "—"),
                "Teléfono":    c.telefono or "—",
                "Email":       c.email or "—",
                "Saldo":       f"${c.saldo:,.2f}",
                "Creado":      c.creado_en.strftime("%Y-%m-%d") if c.creado_en else "—",
            } for c in clientes])
            st.dataframe(df, use_container_width=True, hide_index=True)
            activos    = sum(1 for c in clientes if c.estado == "ACTIVO")
            suspendidos= sum(1 for c in clientes if c.estado == "SUSPENDIDO")
            cerrados   = sum(1 for c in clientes if c.estado == "CERRADO")
            st.caption(
                f"Total: {len(clientes)} · "
                f"🟢 Activos: {activos} · "
                f"🟡 Suspendidos: {suspendidos} · "
                f"🔴 Cerrados: {cerrados}"
            )
        else:
            alert("info", "No hay clientes con ese estado.")

    # ── Tab 2: Nuevo cliente ──────────────────────────────────
    if "➕ Nuevo cliente" in tab_map:
        with tab_map["➕ Nuevo cliente"]:
            tipos_cuenta = session.query(TipoCuenta).filter_by(activo=True).order_by(TipoCuenta.nombre).all()
            sucursales   = session.query(Sucursal).filter_by(activa=True).order_by(Sucursal.nombre).all()

            with st.form("nuevo_cliente", clear_on_submit=True):
                st.markdown("**📋 Datos de identificación (KYC)**")
                col1, col2 = st.columns(2)
                with col1:
                    nombre        = st.text_input("Nombre completo *")
                    tipo_doc      = st.selectbox("Tipo documento", ["DUI", "Pasaporte", "NIT", "Carnet residente"])
                    documento     = st.text_input("Número de documento (DUI)")
                    nit_input     = st.text_input("NIT")
                with col2:
                    tc_nombres    = [t.nombre for t in tipos_cuenta]
                    tc_idx        = st.selectbox("Tipo de cuenta *", range(len(tc_nombres)),
                                                  format_func=lambda i: tc_nombres[i])
                    saldo_inicial = st.number_input("Saldo inicial ($)", min_value=0.0, step=100.0, max_value=500_000.0)
                    fecha_nac     = st.text_input("Fecha de nacimiento (YYYY-MM-DD)")
                    suc_nombres   = [s.nombre for s in sucursales]
                    suc_idx       = st.selectbox("Sucursal", range(len(suc_nombres)),
                                                  format_func=lambda i: suc_nombres[i]) if suc_nombres else None

                st.markdown("**👤 Datos económicos (KYC)**")
                col3, col4 = st.columns(2)
                with col3:
                    profesion     = st.text_input("Profesión / Ocupación")
                    ingresos      = st.number_input("Ingresos mensuales ($)", min_value=0.0, step=50.0)
                with col4:
                    telefono      = st.text_input("Teléfono")
                    email         = st.text_input("Correo electrónico")

                direccion = st.text_area("Dirección", height=70)

                # Mostrar info del tipo de cuenta seleccionado
                if tipos_cuenta:
                    tc_sel = tipos_cuenta[tc_idx]
                    st.info(f"ℹ️ **{tc_sel.nombre}** — Tasa: {float(tc_sel.tasa_interes)*100:.2f}% anual | "
                            f"Saldo mínimo: ${float(tc_sel.saldo_minimo):,.2f} | "
                            f"Comisión: {'Sí' if tc_sel.cobra_comision else 'No'}")

                enviado = st.form_submit_button("✅ Crear cliente", type="primary")

            if enviado:
                if not nombre.strip():
                    alert("error", "✗ El nombre no puede estar vacío.")
                else:
                    tc_id  = tipos_cuenta[tc_idx].id if tipos_cuenta else None
                    tc_nom = tipos_cuenta[tc_idx].nombre if tipos_cuenta else "Ahorro"
                    suc_id = sucursales[suc_idx].id if sucursales and suc_idx is not None else None
                    ok, msg = crear_cliente(
                        session, nombre, tc_nom, saldo_inicial,
                        documento=documento, tipo_documento=tipo_doc,
                        telefono=telefono, email=email,
                        direccion=direccion, fecha_nacimiento=fecha_nac,
                        nit=nit_input, profesion=profesion,
                        ingresos_mensuales=ingresos,
                        tipo_cuenta_id=tc_id, sucursal_id=suc_id,
                    )
                    if ok:
                        msg_str = f"Cliente '{msg.nombre}' creado — cuenta {msg.num_cuenta}"
                    else:
                        msg_str = msg
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg_str)
                    if ok:
                        st.rerun()

    # ── Tab 3: Editar cliente ─────────────────────────────────
    if "✏️ Editar" in tab_map:
        with tab_map["✏️ Editar"]:
            clientes_edit = session.query(Cliente).filter(
                Cliente.estado != "CERRADO"
            ).order_by(Cliente.nombre).all()

            if not clientes_edit:
                alert("info", "No hay clientes editables.")
            else:
                sel_e = st.selectbox(
                    "Seleccionar cliente a editar",
                    clientes_edit,
                    format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}",
                    key="edit_sel",
                )
                if sel_e:
                    with st.form("editar_cliente", clear_on_submit=False):
                        st.markdown(f"**Editando:** {sel_e.nombre} · `{sel_e.num_cuenta}`")
                        col1, col2 = st.columns(2)
                        with col1:
                            nuevo_nombre = st.text_input("Nombre completo", value=sel_e.nombre or "")
                            tipos_cuenta_edit = session.query(TipoCuenta).filter_by(activo=True).order_by(TipoCuenta.nombre).all()
                            tc_nombres_edit   = [tc.nombre for tc in tipos_cuenta_edit]
                            tipo_actual = sel_e.tipo or ""
                            tc_idx_actual     = tc_nombres_edit.index(tipo_actual) if tipo_actual in tc_nombres_edit else 0
                            nuevo_tipo_idx = st.selectbox(
                                "Tipo de cuenta",
                                range(len(tc_nombres_edit)),
                                index=tc_idx_actual,
                                format_func=lambda i: tc_nombres_edit[i],
                                key=f"edit_tipo_cuenta_{sel_e.id}",  # <-- key único por cliente
                            )
                            nuevo_tipo_doc = st.selectbox(
                                "Tipo documento",
                                ["DUI", "Pasaporte", "NIT", "Carnet residente"],
                                index=["DUI","Pasaporte","NIT","Carnet residente"].index(
                                    sel_e.tipo_documento or "DUI"
                                ) if sel_e.tipo_documento in ["DUI","Pasaporte","NIT","Carnet residente"] else 0,
                            )
                            nuevo_doc = st.text_input("Número de documento", value=sel_e.documento or "")
                        with col2:
                            nueva_fecha = st.text_input("Fecha nacimiento (YYYY-MM-DD)", value=sel_e.fecha_nacimiento or "")
                            nuevo_tel   = st.text_input("Teléfono", value=sel_e.telefono or "")
                            nuevo_email = st.text_input("Correo electrónico", value=sel_e.email or "")
                        nueva_dir = st.text_area("Dirección", value=sel_e.direccion or "", height=80)
                        guardado = st.form_submit_button("💾 Guardar cambios", type="primary")

                    if guardado:
                        ok, msg = editar_cliente(
                            session, sel_e.id,
                            nombre=nuevo_nombre,
                            tipo=tc_nombres_edit[nuevo_tipo_idx],
                            tipo_cuenta_id=tipos_cuenta_edit[nuevo_tipo_idx].id,
                            tipo_documento=nuevo_tipo_doc,
                            documento=nuevo_doc,
                            fecha_nacimiento=nueva_fecha,
                            telefono=nuevo_tel,
                            email=nuevo_email,
                            direccion=nueva_dir,
                        )
                        alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                        if ok:
                            st.rerun()

    # ── Tab 4: Estado de cuenta (suspender / reactivar / cerrar) ─
    if "🔒 Estado cuenta" in tab_map:
        with tab_map["🔒 Estado cuenta"]:
            clientes_estado = session.query(Cliente).order_by(Cliente.nombre).all()
            if not clientes_estado:
                alert("info", "No hay clientes registrados.")
            else:
                sel_s = st.selectbox(
                    "Seleccionar cliente",
                    clientes_estado,
                    format_func=lambda c: (
                        f"{'🟢' if c.estado=='ACTIVO' else '🟡' if c.estado=='SUSPENDIDO' else '🔴'}"
                        f" [{c.num_cuenta}] {c.nombre}"
                    ),
                    key="estado_sel",
                )
                if sel_s:
                    st.markdown(
                        f"**Estado actual:** "
                        f"{'🟢 ACTIVO' if sel_s.estado=='ACTIVO' else '🟡 SUSPENDIDO' if sel_s.estado=='SUSPENDIDO' else '🔴 CERRADO'}"
                        f"  —  Saldo: **${sel_s.saldo:,.2f}**"
                    )
                    if sel_s.motivo_cierre:
                        st.caption(f"Motivo: {sel_s.motivo_cierre}")

                    st.divider()

                    if sel_s.estado == "ACTIVO":
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.markdown("**Suspender cuenta**")
                            motivo_sus = st.text_input("Motivo de suspensión", key="mot_sus")
                            if st.button("🟡 Suspender", key="btn_sus"):
                                ok, msg = suspender_cliente(session, sel_s.id, motivo_sus)
                                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                                if ok: st.rerun()
                        with col_b:
                            st.markdown("**Cerrar cuenta permanentemente**")
                            st.caption("⚠️ Requiere saldo $0.00 y sin préstamos activos. Irreversible.")
                            motivo_cie = st.text_input("Motivo de cierre", key="mot_cie")
                            if st.button("🔴 Cerrar cuenta", key="btn_cie", type="primary"):
                                ok, msg = cerrar_cuenta(session, sel_s.id, motivo_cie)
                                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                                if ok: st.rerun()

                    elif sel_s.estado == "SUSPENDIDO":
                        if st.button("🟢 Reactivar cuenta", key="btn_react", type="primary"):
                            ok, msg = reactivar_cliente(session, sel_s.id)
                            alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                            if ok: st.rerun()
                        st.markdown("**Cerrar cuenta permanentemente**")
                        st.caption("⚠️ Requiere saldo $0.00 y sin préstamos activos.")
                        motivo_cie2 = st.text_input("Motivo de cierre", key="mot_cie2")
                        if st.button("🔴 Cerrar cuenta", key="btn_cie2"):
                            ok, msg = cerrar_cuenta(session, sel_s.id, motivo_cie2)
                            alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                            if ok: st.rerun()

                    else:  # CERRADO
                        alert("info", "Esta cuenta está cerrada. No se pueden realizar más operaciones.")

    # ── Tab 5: Historial completo (expediente bancario) ──────
    with tab_map["📜 Historial"]:
        clientes_h = session.query(Cliente).order_by(Cliente.nombre).all()
        if clientes_h:
            sel = st.selectbox(
                "Seleccionar cliente",
                clientes_h,
                format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}  —  ${c.saldo:,.2f}",
                key="hist_sel",
            )
            exp = historial_cliente(session, sel.id)
            if not exp:
                alert("error", "No se pudo cargar el historial.")
            else:
                # ── Resumen de cuenta ──
                kyc_icon = "✅" if sel.kyc_completo else "⚠️"
                score_color = "#2ecc71" if sel.score_credito >= 650 else "#f39c12" if sel.score_credito >= 500 else "#e74c3c"
                st.markdown(f"""
<div style="background:#1e1e2e;border:1px solid #3d3b3c;border-radius:10px;padding:16px;margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <div style="font-size:1.1rem;font-weight:700;color:#e8e4d9">{sel.nombre}</div>
      <div style="color:#8a8694;font-size:.82rem">Cuenta: <code>{sel.num_cuenta}</code> · Tipo: {sel.tipo} · {kyc_icon} KYC</div>
      <div style="color:#8a8694;font-size:.82rem">DUI: {sel.documento or "—"} · NIT: {sel.nit or "—"} · Tel: {sel.telefono or "—"}</div>
      <div style="color:#8a8694;font-size:.82rem">Profesión: {sel.profesion or "—"} · Ingresos: ${float(sel.ingresos_mensuales or 0):,.2f}/mes</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:1.4rem;font-weight:700;color:#7ec8c8">${float(sel.saldo):,.2f}</div>
      <div style="font-size:.82rem;color:{score_color}">Score: {sel.score_credito or 500}</div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

                # ── Score crediticio ──
                score_tab, movs_tab, prest_tab, tarj_tab, dpf_tab, benef_tab = st.tabs([
                    "📊 Score", "💳 Movimientos", "🏷️ Préstamos",
                    "💳 Tarjetas", "📑 Plazo Fijo", "⭐ Beneficiarios"
                ])

                with score_tab:
                    sc = exp.get("score")
                    col_s1, col_s2 = st.columns([1, 2])
                    with col_s1:
                        if st.button("🔄 Recalcular Score", key=f"score_btn_{sel.id}"):
                            sc = calcular_score(session, sel.id)
                            session.commit()
                            st.rerun()
                    if sc:
                        score_val = sc.score
                        cat_color = {"Excelente": "#2ecc71", "Bueno": "#27ae60",
                                     "Regular": "#f39c12", "Malo": "#e67e22", "Muy Malo": "#e74c3c"}
                        color_sc = cat_color.get(sc.categoria, "#8a8694")
                        st.markdown(f"""
<div style="background:#1e1e2e;border:2px solid {color_sc};border-radius:12px;padding:20px;text-align:center">
  <div style="font-size:3rem;font-weight:900;color:{color_sc}">{score_val}</div>
  <div style="font-size:1.1rem;color:{color_sc};font-weight:600">{sc.categoria}</div>
  <div style="color:#8a8694;font-size:.82rem;margin-top:8px">
    Pagos puntuales: {sc.pagos_puntuales} · Pagos atrasados: {sc.pagos_atrasados} ·
    Préstamos activos: {sc.prestamos_activos}
  </div>
</div>""", unsafe_allow_html=True)
                    else:
                        st.info("Presiona 'Recalcular Score' para generar el score crediticio.")

                with movs_tab:
                    movs = exp["movimientos"]
                    col_a, col_b = st.columns([2, 1])
                    with col_a:
                        st.markdown(f"**{len(movs)} movimiento(s)**")
                    with col_b:
                        color = "#10B981" if sel.saldo >= 0 else "#F87171"
                        st.markdown(f"<div style='text-align:right;font-family:DM Mono,monospace;font-size:1.1rem;font-weight:600;color:{color}'>${float(sel.saldo):,.2f}</div>", unsafe_allow_html=True)
                    render_movimientos(movs)
                    st.divider()
                    _botones_exportar_historial(session, sel, movs)

                with prest_tab:
                    prest_list = exp["prestamos"]
                    if prest_list:
                        for p in prest_list:
                            estado_c = "#2ecc71" if p.estado == "ACTIVO" else "#8a8694"
                            st.markdown(f"""
<div style="background:#1e1e2e;border-left:4px solid {estado_c};padding:10px 14px;border-radius:6px;margin-bottom:8px">
  <b>Préstamo #{p.id}</b> — ${float(p.monto):,.2f} | Saldo: ${float(p.saldo_pendiente):,.2f}
  | Estado: <span style="color:{estado_c}">{p.estado}</span>
  | Mora: ${float(p.mora_acumulada or 0):,.2f} | Clasificación: {p.clasificacion or "—"}
</div>""", unsafe_allow_html=True)
                    else:
                        st.info("No tiene préstamos registrados.")

                with tarj_tab:
                    td = exp["tarjetas_debito"]
                    tc = exp["tarjetas_credito"]
                    if td:
                        st.markdown("**Tarjetas de Débito**")
                        for t in td:
                            st.markdown(f"🟦 `****{t.numero[-4:]}` · Vence: {t.vencimiento} · Estado: {t.estado}")
                    if tc:
                        st.markdown("**Tarjetas de Crédito**")
                        for t in tc:
                            st.markdown(f"🟨 `****{t.numero[-4:]}` · Límite: ${float(t.limite):,.2f} · Usado: ${float(t.saldo_usado):,.2f} · Estado: {t.estado}")
                    if not td and not tc:
                        st.info("No tiene tarjetas emitidas.")

                with dpf_tab:
                    dpfs = exp["depositos_plazo"]
                    if dpfs:
                        for d in dpfs:
                            st.markdown(f"📑 **{d.num_certificado}** — ${float(d.monto):,.2f} · {d.plazo_meses} meses · {float(d.tasa_anual)*100:.1f}% · Vence: {d.fecha_vencimiento} · **{d.estado}**")
                    else:
                        st.info("No tiene depósitos a plazo fijo.")

                with benef_tab:
                    benefs = exp["beneficiarios"]
                    if benefs:
                        for b in benefs:
                            col_b1, col_b2 = st.columns([3, 1])
                            with col_b1:
                                st.markdown(f"⭐ **{b.alias}** — Cuenta: `{b.cuenta_destino}` ({b.nombre_destino or '—'})")
                            with col_b2:
                                if st.button("🗑️", key=f"del_benef_{b.id}"):
                                    eliminar_beneficiario(session, b.id, sel.id)
                                    session.commit()
                                    st.rerun()
                    else:
                        st.info("No tiene beneficiarios registrados.")
                    st.divider()
                    st.markdown("**➕ Agregar beneficiario**")
                    col_ba, col_bb = st.columns(2)
                    with col_ba:
                        nueva_cuenta = st.text_input("Número de cuenta destino", key=f"benef_cuenta_{sel.id}")
                    with col_bb:
                        nuevo_alias  = st.text_input("Alias", key=f"benef_alias_{sel.id}")
                    if st.button("Agregar", key=f"btn_benef_{sel.id}"):
                        ok, result = agregar_beneficiario(session, sel.id, nueva_cuenta, nuevo_alias)
                        if ok:
                            session.commit()
                            st.success("✅ Beneficiario agregado")
                            st.rerun()
                        else:
                            st.error(result)
        else:
            alert("info", "No hay clientes registrados.")


# ─── Vista clientes (solo lectura — Gerente) ─────────────────
def vista_clientes_readonly(session):
    render_header("👥", "Clientes", "Vista de solo lectura")
    alert("info", "Tu rol solo permite visualizar clientes, no realizar cambios.")
    clientes = session.query(Cliente).order_by(Cliente.nombre).all()
    ESTADO_ICON = {"ACTIVO": "🟢", "SUSPENDIDO": "🟡", "CERRADO": "🔴"}
    if clientes:
        df = pd.DataFrame([{
            "Nº Cuenta":  c.num_cuenta or "—",
            "Nombre":     c.nombre,
            "Tipo":       c.tipo.capitalize(),
            "Estado":     ESTADO_ICON.get(c.estado,"❓") + " " + (c.estado or "—"),
            "Documento":  (c.tipo_documento or "") + " " + (c.documento or "—"),
            "Saldo":      f"${c.saldo:,.2f}",
            "Creado":     c.creado_en.strftime("%Y-%m-%d") if c.creado_en else "—",
        } for c in clientes])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"Total: {len(clientes)} clientes")
    else:
        alert("info", "No hay clientes registrados.")


# ─── Vista préstamos (solo lectura — Gerente) ────────────────
def vista_prestamos_readonly(session):
    render_header("🏷️", "Préstamos", "Vista de solo lectura")
    alert("info", "Tu rol solo permite visualizar préstamos.")
    prestamos = (
        session.query(Prestamo)
        .options(selectinload(Prestamo.cliente))
        .order_by(Prestamo.fecha.desc())
        .all()
    )
    if prestamos:
        df = pd.DataFrame([{
            "ID":         p.id,
            "Cliente ID": p.cliente_id,
            "Monto":      f"${p.monto:,.2f}",
            "Interés":    f"${p.interes:,.2f}",
            "Pendiente":  f"${p.saldo_pendiente:,.2f}",
            "Estado":     p.estado,
            "Fecha":      p.fecha.strftime("%Y-%m-%d") if p.fecha else "—",
        } for p in prestamos])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        alert("info", "No hay préstamos registrados.")


def vista_operaciones(session):
    render_header("💵", "Operaciones bancarias")
    # Recuperar usuario activo para auditoría
    usuario_activo = session.query(Usuario).filter_by(
        id=st.session_state.get("usuario_id")
    ).first()

    operacion = st.radio(
        "Operación",
        ["💰 Depósito", "🏧 Retiro", "↔️ Transferencia", "↩️ Reversar operación"],
        horizontal=True,
    )
    clientes = session.query(Cliente).filter_by(estado="ACTIVO").order_by(Cliente.nombre).all()

    if not clientes:
        alert("info", "No hay clientes activos. Cree o reactive un cliente primero.")
        return

    st.divider()

    # ── Depósito ──
    if "Depósito" in operacion:
        st.markdown("**Depósito en ventanilla** — se aplica comisión del 2% sobre el monto bruto.")
        with st.form("deposito_form"):
            sel   = st.selectbox("Cliente", clientes,
                                 format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}  —  ${c.saldo:,.2f}")
            monto = st.number_input("Monto bruto ($)", min_value=1.0, value=100.0, step=100.0,
                                    max_value=500_000.0)
            st.info(
                f"💡  Neto al cliente: **${float(monto)*0.98:,.2f}**  ·  "
                f"Comisión banco: **${float(monto)*0.02:,.2f}**"
            )
            enviado = st.form_submit_button("💰 Realizar depósito", type="primary")
        if enviado:
            ok, msg = depositar(session, sel.id, monto)
            audit(session, usuario_activo, "DEPOSITO",
                  f"Cliente {sel.nombre} ({sel.num_cuenta}) — ${monto:,.2f}",
                  "OK" if ok else "ERROR")
            if ok:
                aml_alerts = verificar_aml(session, sel.id, monto, "Deposito")
                session.commit()
                if aml_alerts:
                    st.warning(f"🚨 AML: Se generó {len(aml_alerts)} alerta(s) de prevención de lavado de dinero.")
            alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
            if ok: st.rerun()

    # ── Retiro ──
    elif "Retiro" in operacion:
        sel = st.selectbox("Cliente", clientes,
                           format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}  —  ${c.saldo:,.2f}",
                           key="retiro_sel")
        color = "#10B981" if sel.saldo > 0 else "#F87171"
        st.markdown(
            f"<div class='saldo-box'>"
            f"<span style='opacity:.6;font-size:.82rem'>Saldo disponible</span>"
            f"<span style='font-size:1.15rem;font-weight:600;"
            f"font-family:DM Mono,monospace;color:{color}'>"
            f"${sel.saldo:,.2f}</span></div>",
            unsafe_allow_html=True,
        )
        if sel.saldo <= 0:
            alert("warning", "El cliente no tiene saldo disponible.")
        else:
            with st.form("retiro_form"):
                monto   = st.number_input("Monto a retirar ($)",
                                          min_value=1.0, value=min(100.0, float(sel.saldo)),
                                          step=10.0)
                enviado = st.form_submit_button("🏧 Realizar retiro", type="primary")
            if enviado:
                # Recargar saldo fresco desde BD para evitar datos desactualizados
                cliente_fresco = session.query(__import__('models', fromlist=['Cliente']).Cliente).filter_by(id=sel.id).first()
                saldo_actual = float(cliente_fresco.saldo) if cliente_fresco else 0.0
                if monto <= 0:
                    alert("error", "✗ El monto a retirar debe ser mayor a cero.")
                elif monto > saldo_actual:
                    alert("error", f"✗ Saldo insuficiente — disponible: ${saldo_actual:,.2f}, solicitado: ${monto:,.2f}.")
                else:
                    ok, msg = retirar(session, sel.id, monto)
                    audit(session, usuario_activo, "RETIRO",
                          f"Cliente {sel.nombre} ({sel.num_cuenta}) — ${monto:,.2f}",
                          "OK" if ok else "ERROR")
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                    if ok: st.rerun()

    # ── Transferencia ──
    elif "Transferencia" in operacion:
        st.markdown("**Transferencia entre cuentas** — comisión del 1% a cargo del origen.")

        # Selector de origen primero para cargar beneficiarios
        origen_sel = st.selectbox("Cuenta origen", clientes,
                                  format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}  —  ${c.saldo:,.2f}",
                                  key="trf_origen_pre")

        # Beneficiarios frecuentes del cliente origen
        benefs = session.query(Beneficiario).filter_by(cliente_id=origen_sel.id, activo=True).all()
        destino_manual = True
        if benefs:
            st.caption(f"⭐ Este cliente tiene {len(benefs)} beneficiario(s) frecuente(s)")
            usar_benef = st.checkbox("Usar beneficiario frecuente", key="trf_usar_benef")
            if usar_benef:
                benef_sel = st.selectbox("Beneficiario", benefs,
                                         format_func=lambda b: f"{b.alias} — {b.cuenta_destino}",
                                         key="trf_benef_sel")
                destino_cliente = session.query(Cliente).filter_by(num_cuenta=benef_sel.cuenta_destino).first()
                destino_manual = False

        with st.form("transferencia_form"):
            col1, col2 = st.columns(2)
            with col1:
                origen = st.selectbox("Confirmar cuenta origen", clientes,
                                      format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}  —  ${c.saldo:,.2f}",
                                      index=clientes.index(origen_sel) if origen_sel in clientes else 0)
            with col2:
                if destino_manual or not benefs:
                    destino = st.selectbox("Cuenta destino", clientes,
                                           format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}  —  ${c.saldo:,.2f}")
                else:
                    destino = destino_cliente
                    st.markdown(f"**Destino (beneficiario):** {benef_sel.alias} `{benef_sel.cuenta_destino}`")
            monto = st.number_input("Monto a transferir ($)", min_value=1.0, value=100.0,
                                    step=100.0, max_value=500_000.0)
            st.info(
                f"💡  Destino recibe: **${float(monto):,.2f}**  ·  "
                f"Comisión (1%): **${float(monto)*0.01:,.2f}**  ·  "
                f"Total descontado del origen: **${float(monto)*1.01:,.2f}**"
            )
            enviado = st.form_submit_button("↔️ Realizar transferencia", type="primary")
        if enviado:
            if origen.id == destino.id:
                alert("error", "✗ Cuenta origen y destino no pueden ser iguales.")
            else:
                ok, msg = transferir(session, origen.id, destino.id, monto)
                audit(session, usuario_activo, "TRANSFERENCIA",
                      f"{origen.nombre} → {destino.nombre} — ${monto:,.2f}",
                      "OK" if ok else "ERROR")
                if ok:
                    aml_alerts = verificar_aml(session, origen.id, monto, "Transferencia")
                    session.commit()
                    if aml_alerts:
                        st.warning(f"🚨 AML: {len(aml_alerts)} alerta(s) de prevención de lavado de dinero generada(s).")
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                if ok: st.rerun()

    # ── Reversar operación ──
    else:
        st.markdown(f"**Reversión de operaciones** — solo disponible dentro de los últimos "
                    f"**{VENTANA_REVERSION_MIN} minutos**. "
                    f"Tipos reversibles: {', '.join(OPERACIONES_REVERSIBLES)}.")
        alert("warning", "⚠️ La reversión genera un asiento contable inverso. "
              "Úsela solo para corregir errores de operación.")

        sel_rev = st.selectbox(
            "Cliente",
            clientes,
            format_func=lambda c: f"[{c.num_cuenta}] {c.nombre}",
            key="rev_cliente",
        )
        if sel_rev:
            from datetime import timedelta
            ventana = datetime.utcnow() - timedelta(minutes=VENTANA_REVERSION_MIN)
            movs_recientes = (
                session.query(Movimiento)
                .filter_by(cliente_id=sel_rev.id)
                .filter(Movimiento.tipo.in_(list(OPERACIONES_REVERSIBLES)))
                .filter(Movimiento.fecha >= ventana)
                .order_by(Movimiento.fecha.desc())
                .all()
            )
            if not movs_recientes:
                alert("info", f"No hay operaciones reversibles en los últimos "
                      f"{VENTANA_REVERSION_MIN} minutos para este cliente.")
            else:
                mov_sel = st.selectbox(
                    "Operación a revertir",
                    movs_recientes,
                    format_func=lambda m: (
                        f"#{m.id} — {m.tipo} — ${float(m.monto):,.2f} "
                        f"— {m.fecha.strftime('%H:%M:%S')}"
                    ),
                    key="rev_mov",
                )
                motivo = st.text_input("Motivo de la reversión *", key="rev_motivo",
                                       placeholder="Ej: Error de digitación del cajero")
                if st.button("↩️ Confirmar reversión", type="primary", key="btn_revertir"):
                    if not motivo.strip():
                        alert("error", "✗ El motivo es obligatorio.")
                    else:
                        ok, msg = revertir_operacion(session, mov_sel.id, motivo)
                        audit(session, usuario_activo, "REVERSION",
                              f"Mov#{mov_sel.id} — {mov_sel.tipo} — ${float(mov_sel.monto):,.2f} — {motivo}",
                              "OK" if ok else "ERROR")
                        alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                        if ok: st.rerun()



def vista_prestamos(session):
    render_header("🏷️", "Gestión de préstamos")
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "➕ Otorgar préstamo",
        "💳 Pagar préstamo",
        "📋 Préstamos activos",
        "🔄 Refinanciamiento",
        "🛡️ Garantías",
    ])
    clientes = session.query(Cliente).order_by(Cliente.nombre).all()

    CLASIFICACIONES = ["Personal", "Vivienda", "Comercio", "Agropecuario"]

    # ── Otorgar ──
    with tab1:
        if not clientes:
            alert("info", "No hay clientes registrados.")
        else:
            capital_banco = saldo_cuenta(session, "Capital Banco")
            total_prest   = float(
                session.query(func.coalesce(func.sum(Prestamo.saldo_pendiente), 0.0))
                .filter(Prestamo.estado == "ACTIVO").scalar()
            )
            capacidad = max(capital_banco - total_prest, 0.0)
            tp = float(tasa_prestamo(session)) * 100
            alert("info",
                  f"Capacidad de crédito disponible: <strong>${capacidad:,.2f}</strong>"
                  f"   ·   (Capital: ${capital_banco:,.2f} – Préstamos activos: ${total_prest:,.2f})"
                  f"   ·   Tasa por defecto: <strong>{tp:.1f}%</strong> (editable por préstamo)")
            with st.form("prestamo_form"):
                col1, col2 = st.columns(2)
                with col1:
                    sel   = st.selectbox("Cliente", clientes,
                                         format_func=lambda c: f"[{c.id}] {c.nombre} · Score: {c.score_credito or 500}")
                    monto = st.number_input("Monto del préstamo ($)", min_value=100.0,
                                            step=500.0, max_value=500_000.0)
                    plazo = st.selectbox("Plazo (meses)", [6,12,24,36,48,60])
                with col2:
                    clasificacion = st.selectbox("Clasificación del crédito", CLASIFICACIONES)
                    tasa_input    = st.number_input("Tasa de interés anual (%)", min_value=1.0,
                                                     max_value=36.0, value=float(tasa_prestamo(session))*100, step=0.5)
                    sucursales    = session.query(Sucursal).filter_by(activa=True).all()
                    suc_sel       = st.selectbox("Sucursal", sucursales,
                                                  format_func=lambda s: s.nombre) if sucursales else None

                interes = monto * (tasa_input / 100)
                cuota   = (monto + interes) / plazo
                st.info(f"Capital: **${monto:,.2f}** · Interés total: **${interes:,.2f}** · "
                        f"Cuota mensual: **${cuota:,.2f}** · Deuda total: **${monto+interes:,.2f}**")
                enviado = st.form_submit_button("✅ Otorgar préstamo", type="primary")
            if enviado:
                # Verificar score antes de otorgar
                score_cliente = sel.score_credito or 500
                if score_cliente < 400:
                    alert("error", f"✗ Score crediticio insuficiente ({score_cliente}). No se puede otorgar el préstamo.")
                else:
                    ok, msg = otorgar_prestamo(session, sel.id, monto, plazo, tasa_anual=tasa_input/100)
                    if ok:
                        # Actualizar clasificación y sucursal en el préstamo recién creado
                        ultimo_p = session.query(Prestamo).filter_by(
                            cliente_id=sel.id, estado="ACTIVO"
                        ).order_by(Prestamo.fecha.desc()).first()
                        if ultimo_p:
                            ultimo_p.clasificacion = clasificacion
                            if suc_sel:
                                ultimo_p.sucursal_id = suc_sel.id
                            session.commit()
                    if ok:
                        msg_str = f"Préstamo #{msg.id} de ${msg.monto:,.2f} otorgado a {plazo} meses"
                    else:
                        msg_str = msg
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg_str)
                    if ok: st.rerun()

    # ── Pagar ──
    with tab2:
        if not clientes:
            alert("info", "No hay clientes registrados.")
        else:
            sel_c = st.selectbox(
                "Cliente",
                clientes,
                format_func=lambda c: f"[{c.id}] {c.nombre}  —  saldo ${c.saldo:,.2f}",
                key="pago_sel",
            )
            p_activo = (
                session.query(Prestamo)
                .filter_by(cliente_id=sel_c.id, estado="ACTIVO")
                .filter(Prestamo.saldo_pendiente > 0)
                .order_by(Prestamo.fecha)
                .first()
            )
            if p_activo:
                int_pend  = round(float(p_activo.interes) - float(p_activo.interes_pagado), 2)
                deuda_tot = round(float(p_activo.saldo_pendiente) + int_pend, 2)
                col_a, col_b, col_c = st.columns(3)
                with col_a: render_metric("Capital pendiente",  f"${p_activo.saldo_pendiente:,.2f}")
                with col_b: render_metric("Interés pendiente",  f"${int_pend:,.2f}")
                with col_c: render_metric("Deuda total",        f"${deuda_tot:,.2f}")
                st.markdown("")
                with st.form("pago_form"):
                    cuota_val = float(p_activo.cuota_mensual) if p_activo.cuota_mensual else 100.0
                    max_pago  = float(min(sel_c.saldo, Decimal(str(deuda_tot))))
                    # Si el saldo residual es menor que la cuota normal (últimos centavos),
                    # usar el saldo residual como valor por defecto y step pequeño.
                    saldo_res = float(p_activo.saldo_pendiente)
                    es_residual = saldo_res < cuota_val * 0.5
                    default_pago = round(min(max_pago, saldo_res if es_residual else cuota_val), 2)
                    step_val = 0.01 if es_residual else round(cuota_val, 2)
                    monto_p = st.number_input(
                        f"Monto a pagar ($)  — cuota mensual: ${cuota_val:,.2f}",
                        min_value=0.01,
                        max_value=max_pago if max_pago > 0 else 0.01,
                        value=default_pago if default_pago > 0 else 0.01,
                        step=step_val,
                    )
                    # Calcular cuántas cuotas cubre el monto ingresado
                    cuotas_cubiertas = int(monto_p // cuota_val) if cuota_val > 0 else 0
                    st.caption(
                        f"Cuota mensual: **${cuota_val:,.2f}** · "
                        f"Monto ingresado cubre: **{cuotas_cubiertas} cuota(s)**"
                    )
                    # BUG CORREGIDO: se pasaba prestamo.id en vez de cliente_id
                    enviado = st.form_submit_button("💳 Registrar pago", type="primary")
                if enviado:
                    ok, msg = pagar_prestamo(session, sel_c.id, monto_p)
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                    if ok: st.rerun()
            else:
                alert("info", f"{sel_c.nombre} no tiene préstamos activos con saldo pendiente.")

    # ── Lista ──
    with tab3:
        activos = (
            session.query(Prestamo)
            .options(
                selectinload(Prestamo.cuotas),
                selectinload(Prestamo.cliente),
            )
            .filter(Prestamo.saldo_pendiente > 0)
            .order_by(Prestamo.fecha.desc())
            .all()
        )
        if activos:
            df = pd.DataFrame([{
                "ID":              p.id,
                "Cliente":         p.cliente.nombre,
                "Monto Original":  f"${p.monto:,.2f}",
                "Interés Total":   f"${p.interes:,.2f}",
                "Int. Devengado":  f"${p.interes_devengado:,.2f}",
                "Int. Pagado":     f"${p.interes_pagado:,.2f}",
                "Capital Pend.":   f"${p.saldo_pendiente:,.2f}",
                "Estado":          p.estado,
                "Fecha":           p.fecha.strftime("%Y-%m-%d") if p.fecha else "—",
                "Plazo":           p.plazo_meses,
                "Cuota":           f"${p.cuota_mensual:,.2f}",
                "Vencimiento":     p.fecha_vencimiento,
                "Mora ($)":        f"${float(p.mora_acumulada or 0):,.2f}",
            } for p in activos])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # ── Tabla de amortización por préstamo ──
            st.divider()
            st.markdown("**📋 Tabla de amortización**")
            sel_p = st.selectbox(
                "Seleccionar préstamo",
                activos,
                format_func=lambda p: f"#{p.id} — {p.cliente.nombre} (${float(p.saldo_pendiente):,.2f} pendiente)",
                key="sel_amort",
            )
            if sel_p and sel_p.cuotas:
                cuotas_ord = sorted(sel_p.cuotas, key=lambda c: c.numero_cuota)
                df_cuotas = pd.DataFrame([{
                    "#":           c.numero_cuota,
                    "Vencimiento": str(c.fecha_vencimiento) if c.fecha_vencimiento else "—",
                    "Capital ($)": f"${float(c.capital):,.2f}",
                    "Interés ($)": f"${float(c.interes):,.2f}",
                    "Cuota ($)":   f"${float(c.monto_cuota):,.2f}",
                    "Saldo ($)":   f"${float(c.saldo_restante):,.2f}",
                    "Estado":      c.estado,
                } for c in cuotas_ord])
                st.dataframe(df_cuotas, use_container_width=True, hide_index=True)
                _boton_exportar_amortizacion(session, sel_p, sel_p.cliente)

            st.divider()
            st.markdown("**📅 Devengo de intereses mensual**")
            st.caption(
                "Registra 1/12 del interés anual de cada préstamo como "
                "Intereses x Cobrar (activo) e Ingresos Intereses."
            )
            col_dev, col_mora = st.columns(2)
            with col_dev:
                if st.button("📅 Devengar intereses del período", type="primary"):
                    ok, msg = devengar_interes(session)
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                    if ok: st.rerun()
            with col_mora:
                with col_mora:
                    if st.button("⚠️ Actualizar cuotas vencidas (mora)"):
                        session.expire_all()
                        n = actualizar_cuotas_vencidas(session)
                        session.commit()
                        session.expire_all()
                        n_mora, msg_mora = calcular_mora(session)
                        st.success(f"✅ {n} cuota(s) marcadas como VENCIDA · {msg_mora}")
                        st.rerun()                
        else:
            alert("info", "No hay préstamos activos con saldo pendiente.")

    # ── Refinanciamiento ──
    with tab4:
        st.markdown("### 🔄 Refinanciamiento de Préstamos")
        st.caption("Convierte el saldo pendiente de un préstamo activo en un nuevo plan de pagos.")
        prestamos_activos = (
            session.query(Prestamo)
            .filter(Prestamo.estado == "ACTIVO", Prestamo.saldo_pendiente > 0)
            .options(selectinload(Prestamo.cliente))
            .order_by(Prestamo.fecha.desc()).all()
        )
        if not prestamos_activos:
            alert("info", "No hay préstamos activos para refinanciar.")
        else:
            p_sel = st.selectbox(
                "Seleccionar préstamo a refinanciar",
                prestamos_activos,
                format_func=lambda p: f"#{p.id} — {p.cliente.nombre} · Saldo: ${float(p.saldo_pendiente):,.2f} · Plazo orig: {p.plazo_meses} meses",
                key="refi_sel",
            )
            if p_sel:
                st.info(f"Saldo pendiente actual: **${float(p_sel.saldo_pendiente):,.2f}** · Mora: **${float(p_sel.mora_acumulada or 0):,.2f}**")
                col_r1, col_r2 = st.columns(2)
                with col_r1:
                    nuevo_plazo = st.selectbox("Nuevo plazo (meses)", [12, 24, 36, 48, 60], key="refi_plazo")
                with col_r2:
                    nueva_tasa  = st.number_input("Nueva tasa anual (%)", min_value=1.0, max_value=36.0,
                                                   value=10.0, step=0.5, key="refi_tasa")
                nueva_cuota = float(p_sel.saldo_pendiente) * (1 + nueva_tasa/100) / nuevo_plazo
                st.info(f"Nueva cuota estimada: **${nueva_cuota:,.2f}**/mes durante {nuevo_plazo} meses")
                if st.button("✅ Confirmar Refinanciamiento", type="primary", key="btn_refi"):
                    ok, result = refinanciar_prestamo(session, p_sel.id, nuevo_plazo, nueva_tasa/100)
                    if ok:
                        session.commit()
                        st.success(f"✅ Préstamo #{result.id} creado. Cuota: ${float(result.cuota_mensual):,.2f}/mes")
                        st.rerun()
                    else:
                        st.error(result)

    # ── Garantías ──
    with tab5:
        st.markdown("### 🛡️ Garantías de Préstamos")
        todos_prestamos = (
            session.query(Prestamo)
            .options(selectinload(Prestamo.cliente), selectinload(Prestamo.garantias))
            .filter(Prestamo.estado.in_(["ACTIVO", "REFINANCIADO"]))
            .order_by(Prestamo.fecha.desc()).all()
        )
        if not todos_prestamos:
            alert("info", "No hay préstamos registrados.")
        else:
            pg_sel = st.selectbox(
                "Préstamo",
                todos_prestamos,
                format_func=lambda p: f"#{p.id} — {p.cliente.nombre} · ${float(p.monto):,.2f} · {p.estado}",
                key="gar_psel",
            )
            if pg_sel:
                # Mostrar garantías existentes
                if pg_sel.garantias:
                    st.markdown("**Garantías registradas:**")
                    for g in pg_sel.garantias:
                        if g.activa:
                            st.markdown(f"🛡️ **{g.tipo.upper()}** — {g.descripcion} · "
                                        f"Valor: ${float(g.valor_estimado):,.2f} · Reg: {g.numero_registro or '—'}")
                else:
                    st.caption("Este préstamo no tiene garantías registradas.")

                st.divider()
                st.markdown("**➕ Agregar garantía**")
                col_g1, col_g2 = st.columns(2)
                with col_g1:
                    tipo_g  = st.selectbox("Tipo de garantía", ["vehiculo", "casa", "terreno", "deposito", "fiador"], key="gar_tipo")
                    valor_g = st.number_input("Valor estimado ($)", min_value=0.0, step=100.0, key="gar_val")
                with col_g2:
                    desc_g  = st.text_input("Descripción", key="gar_desc")
                    reg_g   = st.text_input("Número de registro (matrícula, escritura...)", key="gar_reg")
                if st.button("➕ Agregar Garantía", key="btn_gar"):
                    if not desc_g.strip():
                        st.error("La descripción es obligatoria.")
                    else:
                        ok, result = agregar_garantia(session, pg_sel.id, tipo_g, desc_g, valor_g, reg_g)
                        if ok:
                            session.commit()
                            st.success(f"✅ Garantía agregada al préstamo #{pg_sel.id}")
                            st.rerun()
                        else:
                            st.error(result)


def vista_reportes(session):
    render_header("📈", "Reportes contables")

    col1, col2 = st.columns(2)
    with col1: fecha_ini = st.date_input("Fecha inicio", value=datetime(2025, 1, 1))
    with col2: fecha_fin = st.date_input("Fecha fin",    value=datetime.now())

    inicio = datetime.combine(fecha_ini, datetime.min.time())
    fin    = datetime.combine(fecha_fin, datetime.max.time())

    st.divider()
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Estado de resultados",
        "📋 Balance general",
        "📒 Libro mayor",
        "📰 Libro diario",
    ])

    # ── Estado de resultados ──
    with tab1:
        st.markdown("#### Estado de Resultados")
        ing_int = saldo_cuenta(session, "Ingresos Intereses")
        ing_com = saldo_cuenta(session, "Ingresos Comisiones")
        total   = ing_int + ing_com

        col_a, col_b, col_c = st.columns(3)
        with col_a: render_metric("Ingresos por Intereses",  f"${ing_int:,.2f}")
        with col_b: render_metric("Ingresos por Comisiones", f"${ing_com:,.2f}")
        with col_c: render_metric("Utilidad Neta",            f"${total:,.2f}",
                                   delta=f"+${total:,.2f}", delta_type="up")

        st.markdown("")
        data_ing = {
            "Concepto": ["Ingresos Intereses", "Ingresos Comisiones"],
            "Monto":    [ing_int, ing_com],
        }
        if any(v > 0 for v in data_ing["Monto"]):
            fig = px.bar(
                data_ing, x="Concepto", y="Monto",
                color="Concepto", color_discrete_sequence=[COLORES[0], COLORES[1]],
                labels={"Monto": "Monto ($)"},
            )
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                showlegend=False, height=280, margin=dict(l=0,r=0,t=20,b=0),
                yaxis=dict(tickprefix="$"),
            )
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("##### Asientos de ingresos en el período")
        cuentas_ingreso = session.query(CuentaContable).filter_by(categoria="INGRESO").all()
        ids_ingreso = [c.id for c in cuentas_ingreso]
        asientos_ing = (
            session.query(Asiento)
            .join(LineaAsiento)
            .filter(
                LineaAsiento.cuenta_id.in_(ids_ingreso),
                Asiento.fecha >= inicio,
                Asiento.fecha <= fin,
            )
            .order_by(Asiento.fecha.desc())
            .distinct()
            .limit(30)
            .all()
        )
        if asientos_ing:
            rows = []
            for a in asientos_ing:
                for l in a.lineas:
                    if l.cuenta_id in ids_ingreso:
                        rows.append({
                            "Fecha":       a.fecha.strftime("%Y-%m-%d %H:%M"),
                            "Descripción": a.descripcion,
                            "Cuenta":      l.cuenta.nombre,
                            "Crédito":     f"${l.credito:,.2f}",
                        })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            alert("info", "No hay asientos de ingresos en el período seleccionado.")

    # ── Balance general ──
    with tab2:
        st.markdown("#### Balance General")

        # ── ACTIVOS ──
        caja        = saldo_cuenta(session, "Caja General")
        prest_c     = saldo_cuenta(session, "Prestamos x Cobrar")
        int_c       = saldo_cuenta(session, "Intereses x Cobrar")
        prest_mor   = saldo_cuenta(session, "Prestamos Morosos")
        deud_tar    = saldo_cuenta(session, "Deudores por Tarjeta")
        inversiones = saldo_cuenta(session, "Inversiones")
        bienes      = saldo_cuenta(session, "Bienes e Inmuebles")
        provision   = saldo_cuenta(session, "Provision Incobrables")   # resta

        # ── PASIVOS ──
        depositos   = saldo_cuenta(session, "Depositos Clientes")
        ahorro      = saldo_cuenta(session, "Cuentas de Ahorro")
        corrientes  = saldo_cuenta(session, "Cuentas Corrientes")
        plazo_fijo  = saldo_cuenta(session, "Depositos a Plazo Fijo")
        oblig_banco = saldo_cuenta(session, "Obligaciones con Bancos")
        imp_pagar   = saldo_cuenta(session, "Impuestos por Pagar")

        # ── PATRIMONIO ──
        capital     = saldo_cuenta(session, "Capital Banco")
        reservas    = saldo_cuenta(session, "Reservas Legales")
        utilidades  = saldo_cuenta(session, "Utilidades del Ejercicio")

        # ── INGRESOS y GASTOS (resultado del ejercicio) ──
        ing_int     = saldo_cuenta(session, "Ingresos Intereses")
        ing_com     = saldo_cuenta(session, "Ingresos Comisiones")
        ing_tar     = saldo_cuenta(session, "Ingresos Tarjeta Credito")
        ing_mora    = saldo_cuenta(session, "Ingresos por Mora")
        gto_int     = saldo_cuenta(session, "Gastos Intereses")
        gto_op      = saldo_cuenta(session, "Gastos Operativos")
        gto_prov    = saldo_cuenta(session, "Gastos por Provisiones")
        gto_mora    = saldo_cuenta(session, "Gastos por Mora Pagada")

        resultado_neto = (ing_int + ing_com + ing_tar + ing_mora
                          - gto_int - gto_op - gto_prov - gto_mora)

        activos    = caja + prest_c + int_c + prest_mor + deud_tar + inversiones + bienes - provision
        pasivos    = depositos + ahorro + corrientes + plazo_fijo + oblig_banco + imp_pagar
        patrimonio = capital + reservas + utilidades + resultado_neto
        diff       = round(activos - pasivos - patrimonio, 2)

        # ── Mostrar ACTIVOS ──
        st.markdown("**ACTIVOS**")
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a: render_metric("Caja General",           f"${caja:,.2f}")
        with col_b: render_metric("Préstamos x Cobrar",     f"${prest_c:,.2f}")
        with col_c: render_metric("Intereses x Cobrar",     f"${int_c:,.2f}")
        with col_d: render_metric("Deudores Tarjeta",       f"${deud_tar:,.2f}")
        col_e, col_f, col_g, col_h = st.columns(4)
        with col_e: render_metric("Inversiones",            f"${inversiones:,.2f}")
        with col_f: render_metric("Bienes e Inmuebles",     f"${bienes:,.2f}")
        with col_g: render_metric("Préstamos Morosos",      f"${prest_mor:,.2f}")
        with col_h: render_metric("(-) Prov. Incobrables",  f"${-provision:,.2f}")
        st.markdown(f"**TOTAL ACTIVOS: ${activos:,.2f}**")

        st.divider()

        # ── Mostrar PASIVOS ──
        st.markdown("**PASIVOS**")
        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1: render_metric("Depósitos Clientes",    f"${depositos:,.2f}")
        with col_p2: render_metric("Cuentas de Ahorro",     f"${ahorro:,.2f}")
        with col_p3: render_metric("Cuentas Corrientes",    f"${corrientes:,.2f}")
        col_p4, col_p5, col_p6 = st.columns(3)
        with col_p4: render_metric("Depósitos Plazo Fijo",  f"${plazo_fijo:,.2f}")
        with col_p5: render_metric("Oblig. con Bancos",     f"${oblig_banco:,.2f}")
        with col_p6: render_metric("Impuestos por Pagar",   f"${imp_pagar:,.2f}")
        st.markdown(f"**TOTAL PASIVOS: ${pasivos:,.2f}**")

        st.divider()

        # ── Mostrar PATRIMONIO + RESULTADO ──
        st.markdown("**PATRIMONIO + RESULTADO DEL EJERCICIO**")
        col_q1, col_q2, col_q3 = st.columns(3)
        with col_q1: render_metric("Capital Banco",         f"${capital:,.2f}")
        with col_q2: render_metric("Reservas Legales",      f"${reservas:,.2f}")
        with col_q3: render_metric("Utilidades Ejercicio",  f"${utilidades:,.2f}")
        col_q4, col_q5, col_q6, col_q7 = st.columns(4)
        with col_q4: render_metric("Ing. Intereses",        f"${ing_int:,.2f}")
        with col_q5: render_metric("Ing. Comisiones",       f"${ing_com:,.2f}")
        with col_q6: render_metric("(-) Gastos Operativos", f"${-gto_op:,.2f}")
        with col_q7: render_metric("Resultado Neto",        f"${resultado_neto:,.2f}")
        st.markdown(f"**TOTAL PATRIMONIO+RESULTADO: ${patrimonio:,.2f}**")

        st.markdown("")
        if abs(diff) < 0.01:
            alert("success", f"✅ Balance cuadrado — Activos = Pasivos + Patrimonio (diferencia: ${diff:.2f})")
        else:
            alert("error", f"❌ Balance descuadrado — diferencia: ${diff:,.2f} (revisar asientos)")

        # Gráfico de composición de activos
        composicion = {
            "Caja": caja,
            "Préstamos": prest_c,
            "Intereses x Cobrar": int_c,
            "Bienes e Inmuebles": bienes,
            "Inversiones": inversiones,
            "Deudores Tarjeta": deud_tar,
        }
        composicion = {k: v for k, v in composicion.items() if v > 0}
        if composicion:
            fig3 = px.pie(
                values=list(composicion.values()),
                names=list(composicion.keys()),
                color_discrete_sequence=COLORES,
                hole=0.5,
                title="Composición de activos",
            )
            fig3.update_layout(paper_bgcolor="rgba(0,0,0,0)", height=280,
                               margin=dict(l=0,r=0,t=36,b=0))
            st.plotly_chart(fig3, use_container_width=True)

        # Botones de descarga
        st.divider()
        cuentas_data = [
            {"nombre": "Caja General",           "categoria": "ACTIVO",     "saldo": caja},
            {"nombre": "Préstamos x Cobrar",      "categoria": "ACTIVO",     "saldo": prest_c},
            {"nombre": "Intereses x Cobrar",      "categoria": "ACTIVO",     "saldo": int_c},
            {"nombre": "Deudores por Tarjeta",    "categoria": "ACTIVO",     "saldo": deud_tar},
            {"nombre": "Inversiones",             "categoria": "ACTIVO",     "saldo": inversiones},
            {"nombre": "Bienes e Inmuebles",      "categoria": "ACTIVO",     "saldo": bienes},
            {"nombre": "Préstamos Morosos",       "categoria": "ACTIVO",     "saldo": prest_mor},
            {"nombre": "(-) Prov. Incobrables",   "categoria": "ACTIVO",     "saldo": -provision},
            {"nombre": "Depósitos Clientes",      "categoria": "PASIVO",     "saldo": depositos},
            {"nombre": "Cuentas de Ahorro",       "categoria": "PASIVO",     "saldo": ahorro},
            {"nombre": "Cuentas Corrientes",      "categoria": "PASIVO",     "saldo": corrientes},
            {"nombre": "Depósitos Plazo Fijo",    "categoria": "PASIVO",     "saldo": plazo_fijo},
            {"nombre": "Oblig. con Bancos",       "categoria": "PASIVO",     "saldo": oblig_banco},
            {"nombre": "Impuestos por Pagar",     "categoria": "PASIVO",     "saldo": imp_pagar},
            {"nombre": "Capital Banco",           "categoria": "PATRIMONIO", "saldo": capital},
            {"nombre": "Reservas Legales",        "categoria": "PATRIMONIO", "saldo": reservas},
            {"nombre": "Utilidades Ejercicio",    "categoria": "PATRIMONIO", "saldo": utilidades},
            {"nombre": "Ingresos Intereses",      "categoria": "INGRESO",    "saldo": ing_int},
            {"nombre": "Ingresos Comisiones",     "categoria": "INGRESO",    "saldo": ing_com},
            {"nombre": "Ingresos Tarjeta",        "categoria": "INGRESO",    "saldo": ing_tar},
            {"nombre": "Ingresos por Mora",       "categoria": "INGRESO",    "saldo": ing_mora},
            {"nombre": "Gastos Intereses",        "categoria": "GASTO",      "saldo": gto_int},
            {"nombre": "Gastos Operativos",       "categoria": "GASTO",      "saldo": gto_op},
            {"nombre": "Gastos por Provisiones",  "categoria": "GASTO",      "saldo": gto_prov},
            {"nombre": "Gastos por Mora Pagada",  "categoria": "GASTO",      "saldo": gto_mora},
        ]
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            pdf_bal = generar_balance_pdf(cuentas_data)
            st.download_button(
                "📄 Balance PDF",
                data=pdf_bal,
                file_name=f"balance_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
            )
        with col_dl2:
            csv_bal = generar_balance_csv(cuentas_data)
            st.download_button(
                "📊 Balance CSV",
                data=csv_bal,
                file_name=f"balance_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

    # ── Libro mayor ──
    with tab3:
        st.markdown("#### Libro Mayor — detalle por cuenta")
        cuentas = session.query(CuentaContable).order_by(CuentaContable.categoria).all()
        for cuenta in cuentas:
            saldo = cuenta.saldo(session)
            lineas = (
                session.query(LineaAsiento)
                .filter_by(cuenta_id=cuenta.id)
                .join(Asiento)
                .filter(Asiento.fecha >= inicio, Asiento.fecha <= fin)
                .order_by(Asiento.fecha.desc())
                .all()
            )
            with st.expander(
                f"{cuenta.nombre}  [{cuenta.categoria}]  —  saldo: ${saldo:,.2f}",
                expanded=False,
            ):
                if lineas:
                    df_l = pd.DataFrame([{
                        "Fecha":       l.asiento.fecha.strftime("%Y-%m-%d %H:%M"),
                        "Descripción": l.asiento.descripcion,
                        "Débito":      f"${l.debito:,.2f}" if l.debito else "—",
                        "Crédito":     f"${l.credito:,.2f}" if l.credito else "—",
                    } for l in lineas])
                    st.dataframe(df_l, use_container_width=True, hide_index=True)
                else:
                    st.caption("Sin movimientos en el período seleccionado.")

    # ── Libro diario (nuevo) ──
    with tab4:
        st.markdown("#### Libro Diario — todos los asientos en orden cronológico")
        asientos = (
            session.query(Asiento)
            .filter(Asiento.fecha >= inicio, Asiento.fecha <= fin)
            .order_by(Asiento.fecha)
            .all()
        )
        if not asientos:
            alert("info", "No hay asientos en el período seleccionado.")
        else:
            rows = []
            for a in asientos:
                for l in a.lineas:
                    rows.append({
                        "Fecha":       a.fecha.strftime("%Y-%m-%d %H:%M"),
                        "Asiento #":   a.id,
                        "Descripción": a.descripcion,
                        "Cuenta":      l.cuenta.nombre,
                        "Categoría":   l.cuenta.categoria,
                        "Débito":      float(l.debito)  if l.debito  else 0.0,
                        "Crédito":     float(l.credito) if l.credito else 0.0,
                    })
            df_diario = pd.DataFrame(rows)
            total_deb = df_diario["Débito"].sum()
            total_cre = df_diario["Crédito"].sum()

            # Formatear para mostrar
            df_show = df_diario.copy()
            df_show["Débito"]  = df_show["Débito"].apply( lambda x: f"${x:,.2f}" if x else "—")
            df_show["Crédito"] = df_show["Crédito"].apply(lambda x: f"${x:,.2f}" if x else "—")
            st.dataframe(df_show, use_container_width=True, hide_index=True)

            col_td, col_tc, col_diff = st.columns(3)
            with col_td: render_metric("Total Débitos",  f"${total_deb:,.2f}")
            with col_tc: render_metric("Total Créditos", f"${total_cre:,.2f}")
            with col_diff:
                diff_diario = round(total_deb - total_cre, 2)
                render_metric("Diferencia", f"${diff_diario:,.2f}")
            if abs(total_deb - total_cre) < 0.01:
                alert("success", "✅ Libro diario cuadrado en el período.")
            else:
                alert("error", f"❌ Diferencia en libro diario: ${total_deb - total_cre:,.2f}")

    # ── Tabs adicionales ──
    st.divider()
    _vista_reportes_extra(session, inicio, fin)


def vista_reconciliacion(session):
    render_header(
        "🔍", "Reconciliación contable",
        "Verifica que el balance, libro mayor y depósitos estén cuadrados.",
    )
    alert("info",
          "<strong>Revisa 3 invariantes:</strong> "
          "(1) Activos = Pasivos + Patrimonio + Ingresos · "
          "(2) Libro mayor: Σ débitos = Σ créditos · "
          "(3) Depósitos contables = Σ saldos de clientes")
    st.divider()

    if st.button("🔍 Ejecutar reconciliación completa", type="primary"):
        errores, lines = reconciliar(session)
        for line in lines:
            if "✅" in line:
                alert("success", line.strip())
            elif "❌" in line:
                alert("error", line.strip())
            elif line.startswith("═"):
                st.markdown(f"**{line}**")
            elif line.strip():
                st.code(line, language=None)
        st.divider()
        if errores == 0:
            st.balloons()
            alert("success", "🎉 Sistema completamente conciliado — sin errores detectados.")
        else:
            alert("error",
                  f"⚠️ Se encontraron {errores} discrepancia(s). "
                  f"Revisa los asientos marcados con ❌.")

def ejecutar_stress_test(session):
    """
    Stress test autónomo y completo.
    Cubre TODAS las operaciones del sistema:
      Fase 1 — Operaciones básicas (depósito, retiro, transferencia, préstamos)
      Fase 2 — Tarjetas de crédito, depósitos a plazo fijo, cuentas ahorro/corriente
      Fase 3 — Contabilidad avanzada (inversiones, bienes, mora, provisiones, impuestos)
      Fase 4 — Cierre diario, AML, score crediticio, garantías, refinanciamiento
    Crea sus propios datos, verifica integridad y limpia todo al final.
    """
    import time, uuid
    from models import Prestamo as _Prest, CuotaPrestamo as _Cuota
    from models import Movimiento as _Mov, TarjetaCredito as _TC, DepositoPlazoFijo as _DPF
    from operaciones import procesar_consumo_tarjeta, pagar_intereses_dpf

    suffix = uuid.uuid4().hex[:6].upper()
    st.markdown("### 🔥 Stress Test Completo — Todas las Operaciones")
    resultados = []   # lista de (fase, operacion, ok/fail, detalle)

    def check(fase, nombre, ok, msg=""):
        icon = "✅" if ok else "❌"
        resultados.append((fase, nombre, ok, msg))
        return ok

    # ══════════════════════════════════════════════════════════
    # SETUP — crear clientes y datos base
    # ══════════════════════════════════════════════════════════
    with st.spinner("Creando datos de prueba..."):
        # Clientes
        clientes_temp = []
        for nom in [f"_ST_Ana_{suffix}", f"_ST_Luis_{suffix}",
                    f"_ST_Rosa_{suffix}", f"_ST_Pedro_{suffix}"]:
            ok, r = crear_cliente(session, nom, "Ahorro", 5000)
            if ok:
                session.commit(); session.refresh(r)
                clientes_temp.append(r)
        if len(clientes_temp) < 2:
            st.error("❌ No se pudieron crear clientes de prueba."); return
        c1, c2 = clientes_temp[0], clientes_temp[1]
        extra   = clientes_temp[2] if len(clientes_temp) > 2 else c1

    # ══════════════════════════════════════════════════════════
    # FASE 1 — Operaciones bancarias básicas
    # ══════════════════════════════════════════════════════════
    st.markdown("#### Fase 1 — Operaciones Básicas")
    t0 = time.time()
    ops_ok = ops_fail = 0

    for _ in range(200):
        op  = random.choice(["dep","dep","dep","ret","trf","prest","pago"])
        cli = random.choice(clientes_temp)
        session.refresh(cli)
        monto = Decimal(str(random.randint(20, 400)))
        try:
            if op == "dep":
                ok, _ = depositar(session, cli.id, monto)
            elif op == "ret":
                ok, _ = retirar(session, cli.id, monto)
            elif op == "trf":
                otro = random.choice([c for c in clientes_temp if c.id != cli.id])
                ok, _ = transferir(session, cli.id, otro.id, monto)
            elif op == "prest":
                ok, _ = otorgar_prestamo(session, cli.id, monto, random.choice([6,12,24]))
            elif op == "pago":
                ok, _ = pagar_prestamo(session, cli.id, monto)
            else:
                continue
            if ok: ops_ok += 1
            else:  ops_fail += 1
        except Exception:
            ops_fail += 1
            try: session.rollback()
            except: pass

    check("F1","200 ops aleatorias (dep/ret/trf/préstamo/pago)", ops_ok > 0,
          f"{ops_ok} exitosas / {ops_fail} rechazos")

    # Cuentas ahorro y corriente
    ok1, _ = deposito_cuenta_ahorro(session, c1.id, 500)
    check("F1", "Depósito cuenta ahorro", ok1)
    ok2, _ = deposito_cuenta_corriente(session, c2.id, 300)
    check("F1", "Depósito cuenta corriente", ok2)
    try: session.commit()
    except: session.rollback()

    elapsed1 = round(time.time()-t0, 2)

    # ══════════════════════════════════════════════════════════
    # FASE 2 — Tarjetas y Depósitos a Plazo Fijo
    # ══════════════════════════════════════════════════════════
    st.markdown("#### Fase 2 — Tarjetas de Crédito y Depósitos a Plazo Fijo")

    # Tarjeta de crédito
    ok_tc, tc_obj = emitir_tarjeta_credito(session, c1.id, 1000)
    check("F2", "Emitir tarjeta de crédito", ok_tc)
    if ok_tc:
        try: session.commit(); session.refresh(tc_obj)
        except: session.rollback(); ok_tc = False

    if ok_tc:
        try:
            procesar_consumo_tarjeta(session, tc_obj.id, 150)
            check("F2", "Consumo tarjeta de crédito $150", True)
        except Exception as _ex:
            check("F2", "Consumo tarjeta de crédito $150", False, str(_ex))
        ok_com, _ = registrar_comision_tarjeta_credito(session, 5.0, "Comisión ST")
        check("F2", "Comisión tarjeta crédito", ok_com)
        try: session.commit()
        except: session.rollback()

    # Tarjeta de débito
    ok_td, _ = emitir_tarjeta_debito(session, c2.id)
    check("F2", "Emitir tarjeta de débito", ok_td)
    try: session.commit()
    except: session.rollback()

    # Depósito a plazo fijo
    ok_dpf, dpf_obj = crear_deposito_plazo(session, c1.id, 500, 0.06, 3)
    check("F2", "Crear depósito a plazo fijo (6%, 3 meses)", ok_dpf)
    dpf_id = None
    if ok_dpf:
        try: session.commit(); session.refresh(dpf_obj); dpf_id = dpf_obj.id
        except: session.rollback()

    try:
        pagar_intereses_dpf(session, c1.id, 10)
        check("F2", "Pagar intereses DPF", True)
    except Exception as e:
        check("F2", "Pagar intereses DPF", False, str(e))

    # Vencer DPF
    if dpf_id:
        ok_venc, _ = vencer_deposito_plazo(session, dpf_id, "StressTest")
        check("F2", "Vencer depósito a plazo fijo", ok_venc)
        try: session.commit()
        except: session.rollback()

    # ══════════════════════════════════════════════════════════
    # FASE 3 — Contabilidad Avanzada
    # ══════════════════════════════════════════════════════════
    st.markdown("#### Fase 3 — Contabilidad Avanzada")

    # Inversiones
    ok_inv, _ = registrar_inversion(session, 2000, "Inversión ST")
    check("F3", "Registrar inversión", ok_inv)
    ok_liq, _ = liquidar_inversion(session, 2000, 150, "Liquidación ST")
    check("F3", "Liquidar inversión con ganancia $150", ok_liq)
    try: session.commit()
    except: session.rollback()

    # Bienes e inmuebles
    ok_bien, _ = adquirir_bien_inmueble(session, 5000, "Bien ST")
    check("F3", "Adquirir bien inmueble", ok_bien)
    try: session.commit()
    except: session.rollback()

    # Obligaciones con bancos externos
    ok_ob, _ = recibir_prestamo_banco(session, 3000, "Banco Externo ST")
    check("F3", "Recibir préstamo de banco externo", ok_ob)
    ok_pob, _ = pagar_obligacion_banco(session, 500, 50, "Banco Externo ST")
    check("F3", "Pagar obligación banco (capital+interés)", ok_pob)
    try: session.commit()
    except: session.rollback()

    # Provisiones e incobrables
    ok_prov, _ = constituir_provision(session, 200, "Provisión ST")
    check("F3", "Constituir provisión para incobrables", ok_prov)
    try: session.commit()
    except: session.rollback()

    # Préstamo moroso — necesita préstamo activo
    prest_mor = (session.query(_Prest)
                 .filter(_Prest.cliente_id.in_([c.id for c in clientes_temp]),
                         _Prest.estado == "ACTIVO")
                 .first())
    if prest_mor:
        ok_mor, _ = clasificar_prestamo_moroso(session, prest_mor.id)
        check("F3", "Clasificar préstamo como moroso", ok_mor)
        try: session.commit()
        except: session.rollback()
        # Castigar como incobrable
        prest_mor2 = (session.query(_Prest)
                      .filter(_Prest.cliente_id.in_([c.id for c in clientes_temp]),
                              _Prest.estado == "MOROSO")
                      .first())
        if prest_mor2:
            ok_cast, _ = castigar_prestamo_incobrable(session, prest_mor2.id)
            check("F3", "Castigar préstamo incobrable", ok_cast)
            try: session.commit()
            except: session.rollback()
    else:
        check("F3", "Clasificar/castigar moroso", False, "No había préstamo activo disponible")

    # Mora
    ok_mora_c, _ = cobrar_mora_prestamo(session, c2.id, 25)
    check("F3", "Cobrar mora préstamo", ok_mora_c)
    ok_mora_b, _ = registrar_mora_pagada_banco(session, 10, "Mora ST")
    check("F3", "Registrar mora pagada banco", ok_mora_b)
    try: session.commit()
    except: session.rollback()

    # Impuestos
    ok_pim, _ = provisionar_impuesto(session, 300, "Renta ST")
    check("F3", "Provisionar impuesto sobre renta", ok_pim)
    ok_pag_imp, _ = pagar_impuesto(session, 300, "Renta ST")
    check("F3", "Pagar impuesto", ok_pag_imp)
    try: session.commit()
    except: session.rollback()

    # Reserva legal y utilidades
    ok_res, _ = constituir_reserva_legal(session, 100)
    check("F3", "Constituir reserva legal", ok_res)
    ok_util, _ = registrar_utilidad_ejercicio(session, 500)
    check("F3", "Registrar utilidad del ejercicio", ok_util)
    try: session.commit()
    except: session.rollback()

    # Gastos operativos
    ok_gop, _ = registrar_gasto_operativo(session, 200, "Gasto operativo ST")
    check("F3", "Registrar gasto operativo", ok_gop)
    try: session.commit()
    except: session.rollback()

    # ══════════════════════════════════════════════════════════
    # FASE 4 — Extras: Garantías, Refinanciamiento, Score, AML
    # ══════════════════════════════════════════════════════════
    st.markdown("#### Fase 4 — Garantías, Refinanciamiento, Score y AML")

    # Garantías sobre préstamo activo
    prest_activo = (session.query(_Prest)
                    .filter(_Prest.cliente_id.in_([c.id for c in clientes_temp]),
                            _Prest.estado == "ACTIVO")
                    .first())
    if prest_activo:
        ok_gar, _ = agregar_garantia(session, prest_activo.id,
                                     "Inmueble", "Casa ST", 15000, "REG-ST-001")
        check("F4", "Agregar garantía a préstamo", ok_gar)
        try: session.commit()
        except: session.rollback()

        # Refinanciamiento
        ok_ref, _ = refinanciar_prestamo(session, prest_activo.id, 36)
        check("F4", "Refinanciar préstamo (36 meses)", ok_ref)
        try: session.commit()
        except: session.rollback()
    else:
        check("F4", "Garantía/refinanciamiento", False, "No había préstamo activo")

    # Score crediticio
    try:
        sc_obj = calcular_score(session, c1.id)
        check("F4", f"Score crediticio cliente 1", isinstance(sc_obj.score, (int, float)),
            f"Score: {sc_obj.score}")
    except Exception as ex:
        check("F4", "Score crediticio", False, str(ex))

    # AML
    alertas_aml = verificar_aml(session, c2.id, 9500, "deposito")
    check("F4", "Verificación AML (monto alto)", True, "AML ejecutado") 
    try: session.commit()
    except: session.rollback()

    # Devengamiento de intereses
    try:
        devengar_interes(session)
        check("F4", "Devengamiento de intereses", True)
        session.commit()
    except Exception as ex:
        check("F4", "Devengamiento de intereses", False, str(ex))
        session.rollback()

    # Actualizar cuotas vencidas
    try:
        actualizar_cuotas_vencidas(session)
        check("F4", "Actualizar cuotas vencidas", True)
        session.commit()
    except Exception as ex:
        check("F4", "Actualizar cuotas vencidas", False, str(ex))
        session.rollback()

    # Historial cliente
    try:
        hist = historial_cliente(session, c1.id)
        check("F4", "Historial de cliente", bool(hist))
    except Exception as ex:
        check("F4", "Historial de cliente", False, str(ex))

    # ══════════════════════════════════════════════════════════
    # VERIFICACIÓN CONTABLE FINAL
    # ══════════════════════════════════════════════════════════
    st.markdown("#### Verificación Contable Post-Test")
    session.commit()
    errores_rec, lines = reconciliar(session)
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        if "✅" in stripped:   st.success(stripped)
        elif "❌" in stripped: st.error(stripped)
        else:                  st.code(stripped, language=None)

    negativos = [c for c in clientes_temp if c.saldo < 0]
    if negativos:
        for c in negativos:
            st.error(f"🚨 SALDO NEGATIVO: {c.nombre} = ${c.saldo:,.2f}")
    else:
        st.success("✅ Ningún cliente de prueba con saldo negativo")

    # ══════════════════════════════════════════════════════════
    # LIMPIEZA
    # ══════════════════════════════════════════════════════════
    st.markdown("#### Limpieza de Datos Temporales")
    eliminados = 0
    #for c in clientes_temp:
        #try:
         #   pids = [p.id for p in session.query(_Prest).filter_by(cliente_id=c.id).all()]
          #  if pids:
           #     session.query(_Cuota).filter(_Cuota.prestamo_id.in_(pids))                       .delete(synchronize_session=False)
            #    session.query(_Prest).filter(_Prest.id.in_(pids))                       .delete(synchronize_session=False)
            #session.query(_TC).filter_by(cliente_id=c.id).delete(synchronize_session=False)
            #session.query(_DPF).filter_by(cliente_id=c.id).delete(synchronize_session=False)
            #session.query(_Mov).filter_by(cliente_id=c.id).delete(synchronize_session=False)
          #  pass
         #   c.activo = False
        #    eliminados += 1
      #  except Exception as ex:
       #     st.warning(f"⚠️ No se pudo limpiar {c.nombre}: {ex}")
    #try:
     #   session.commit()
     #   st.success(f"✅ {eliminados} clientes temporales eliminados")
    #except Exception as ex:
      #  session.rollback()
      #  st.warning(f"⚠️ Error en limpieza: {ex}")

    # ══════════════════════════════════════════════════════════
    # RESUMEN FINAL
    # ══════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 📊 Resumen del Stress Test")

    import pandas as pd
    df = pd.DataFrame(resultados, columns=["Fase","Operación","OK","Detalle"])
    total    = len(df)
    exitosos = df["OK"].sum()
    fallidos = total - exitosos

    col1, col2, col3 = st.columns(3)
    col1.metric("Total checks", total)
    col2.metric("✅ Exitosos", exitosos)
    col3.metric("❌ Fallidos", fallidos)

    # Tabla coloreada por resultado
    def color_row(row):
        color = "background-color:#1a3a1a" if row["OK"] else "background-color:#3a1a1a"
        return [color]*len(row)

    st.dataframe(
        df[["Fase","Operación","OK","Detalle"]].style.apply(color_row, axis=1),
        use_container_width=True, hide_index=True
    )

    if fallidos == 0:
        st.success("🎉 **Stress Test completado — TODAS las operaciones pasaron**")
    elif fallidos <= 3:
        st.warning(f"⚠️ Stress Test completado con {fallidos} fallo(s) menores")
    else:
        st.error(f"❌ Stress Test completado con {fallidos} fallos — revisar operaciones marcadas")

    st.subheader("Reconciliación post stress test")
    errores, lines = reconciliar(session)
    for line in lines:
        if "✅" in line:
            st.success(line.strip())
        elif "❌" in line:
            st.error(line.strip())
        elif line.strip():
            st.code(line, language=None)

    # ── CHECK 1: Tasas configurables ──
    st.divider()
    st.subheader("⚙️ Verificación de tasas configurables")
    from operaciones import tasa_deposito, tasa_transferencia, tasa_prestamo
    td = float(tasa_deposito(session))
    tt = float(tasa_transferencia(session))
    tp = float(tasa_prestamo(session))
    if 0 <= td <= 1 and 0 <= tt <= 1 and 0 <= tp <= 1:
        alert("success",
              f"✅ Tasas válidas — Depósito: {td*100:.1f}% | "
              f"Transferencia: {tt*100:.1f}% | Préstamo: {tp*100:.1f}%")
    else:
        alert("error", "❌ Alguna tasa está fuera de rango (0–100%)")

    # ── CHECK 2: Alertas del sistema ──
    st.divider()
    st.subheader("🔔 Verificación de alertas")
    from alertas import obtener_todas_alertas, calcular_mora, NIVEL_ERROR, NIVEL_WARNING
    caja = caja_real(session)
    n_mora, msg_mora = calcular_mora(session)
    todas_alertas = obtener_todas_alertas(session, caja)
    errores_alerta  = [a for a in todas_alertas if a["nivel"] == NIVEL_ERROR]
    warnings_alerta = [a for a in todas_alertas if a["nivel"] == NIVEL_WARNING]
    st.info(f"🔔 Sistema de alertas activo — "
            f"{len(todas_alertas)} alerta(s) detectada(s): "
            f"🔴 {len(errores_alerta)} críticas, 🟡 {len(warnings_alerta)} advertencias")
    if n_mora > 0:
        alert("info", f"📋 Mora recalculada para {n_mora} préstamo(s) vencido(s)")
    alert("success", "✅ Módulo de alertas operativo")

    # ── CHECK 3: Exportación PDF y CSV ──
    st.divider()
    st.subheader("📥 Verificación de exportación")
    from exportar import (
        generar_estado_cuenta_pdf, generar_movimientos_csv,
        generar_balance_pdf, generar_balance_csv,
    )
    fallos_export = []
    try:
        clientes_chk = session.query(Cliente).limit(1).all()
        if clientes_chk:
            c_chk = clientes_chk[0]
            movs_chk = session.query(Movimiento).filter_by(
                cliente_id=c_chk.id
            ).limit(10).all()

            pdf_ec = generar_estado_cuenta_pdf(c_chk, movs_chk)
            if not (pdf_ec[:4] == b"%PDF"):
                fallos_export.append("Estado de cuenta PDF inválido")

            csv_mov = generar_movimientos_csv(movs_chk, c_chk)
            if b"Fecha" not in csv_mov:
                fallos_export.append("CSV de movimientos inválido")

        cuentas_test = [
            {"nombre": "Caja",     "categoria": "ACTIVO",     "saldo": caja},
            {"nombre": "Capital",  "categoria": "PATRIMONIO", "saldo": 1000.0},
        ]
        pdf_bal = generar_balance_pdf(cuentas_test)
        if not (pdf_bal[:4] == b"%PDF"):
            fallos_export.append("Balance PDF inválido")

        csv_bal = generar_balance_csv(cuentas_test)
        if b"Caja" not in csv_bal:
            fallos_export.append("Balance CSV inválido")

    except Exception as e:
        fallos_export.append(f"Excepción en exportación: {e}")

    if fallos_export:
        for f_msg in fallos_export:
            alert("error", f"❌ {f_msg}")
    else:
        alert("success", "✅ PDF y CSV generados correctamente (estado de cuenta, balance)")

    # ── Resumen final ──
    st.divider()
    total_checks = 3
    checks_ok = sum([
        errores == 0,
        (0 <= td <= 1 and 0 <= tt <= 1 and 0 <= tp <= 1),
        len(fallos_export) == 0,
    ])
    if checks_ok == total_checks:
        st.balloons()
        alert(
            "success",
            f"🎉 Stress test completo — {total_checks} módulos verificados "
            f"correctamente ({checks_ok}/{total_checks})."
        )
    else:
        alert("warning",
              f"⚠️ {checks_ok}/{total_checks} módulos OK — "
              f"revisa los errores marcados arriba.")


# ─────────────────────────────
# GUARDIA DE PERMISO
# ─────────────────────────────
def _acceso_denegado(permiso):
    st.warning(f"⛔ Tu rol no tiene acceso a esta sección (`{permiso}`).")


# ─────────────────────────────
# PANTALLA: PRIMER ARRANQUE
# ─────────────────────────────
def pantalla_primer_admin(session):
    st.markdown("""
<div style='text-align:center;padding:2rem 0 1rem'>
  <div style='font-size:3rem'>🏦</div>
  <h2 style='margin:.5rem 0 .25rem;font-size:1.4rem;font-weight:600'>Bienvenido a BancoApp</h2>
  <p style='color:#8A8694;font-size:.9rem'>Primera vez · Crea el usuario administrador para comenzar</p>
</div>
""", unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        with st.form("setup_admin"):
            st.markdown("**Datos del administrador**")
            nombre   = st.text_input("Nombre completo *")
            username = st.text_input("Nombre de usuario *", placeholder="ej. admin")
            pwd1     = st.text_input("Contraseña *", type="password")
            pwd2     = st.text_input("Confirmar contraseña *", type="password")
            enviado  = st.form_submit_button("✅ Crear administrador", type="primary",
                                             use_container_width=True)
        if enviado:
            if not nombre.strip() or not username.strip() or not pwd1:
                st.error("✗ Completa todos los campos.")
            elif pwd1 != pwd2:
                st.error("✗ Las contraseñas no coinciden.")
            elif len(pwd1) < 6:
                st.error("✗ La contraseña debe tener al menos 6 caracteres.")
            else:
                ok, msg = registrar_primer_admin(session, username, nombre, pwd1)
                if ok:
                    st.success(f"✓ {msg}. Recarga la página para iniciar sesión.")
                    st.rerun()
                else:
                    st.error(f"✗ {msg}")


# ─────────────────────────────
# PANTALLA: LOGIN
# ─────────────────────────────
def pantalla_login(session):
    st.markdown("""
<div style='text-align:center;padding:2rem 0 1rem'>
  <div style='font-size:3rem'>🏦</div>
  <h2 style='margin:.5rem 0 .25rem;font-size:1.4rem;font-weight:600'>BancoApp</h2>
  <p style='color:#8A8694;font-size:.9rem'>Ingresa tus credenciales para continuar</p>
</div>
""", unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        with st.form("login_form"):
            username = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            enviado  = st.form_submit_button("🔐 Iniciar sesión", type="primary",
                                             use_container_width=True)
        if enviado:
            usuario, msg = login(session, username, password)
            if usuario:
                st.session_state["usuario_id"]       = usuario.id
                st.session_state["usuario_nombre"]   = usuario.nombre
                st.session_state["usuario_rol"]      = usuario.rol
                st.session_state["usuario_username"] = usuario.username
                st.session_state["session_inicio"]   = datetime.utcnow().isoformat()
                st.rerun()
            else:
                st.error(f"✗ {msg}")


# ─────────────────────────────
# VISTA: GESTIÓN DE USUARIOS  (solo ADMIN)
# ─────────────────────────────
def vista_usuarios(session, usuario_actual):
    render_header("👤", "Gestión de usuarios",
                  "Administra los usuarios del sistema bancario")

    tab1, tab2, tab3 = st.tabs(["👥 Lista de usuarios", "➕ Nuevo usuario", "🔑 Mi contraseña"])

    ROL_ICONS = {"ADMIN": "🔑", "GERENTE": "📊", "CAJERO": "💵", "AUDITOR": "🔍"}

    with tab1:
        usuarios = session.query(Usuario).order_by(Usuario.username).all()
        if usuarios:
            df = pd.DataFrame([{
                "Usuario":      u.username,
                "Nombre":       u.nombre,
                "Rol":          ROL_ICONS.get(u.rol, "❓") + " " + u.rol,
                "Estado":       "🟢 Activo" if u.activo else "🔴 Inactivo",
                "Último login": u.ultimo_login.strftime("%Y-%m-%d %H:%M") if u.ultimo_login else "—",
                "Creado":       u.creado_en.strftime("%Y-%m-%d") if u.creado_en else "—",
            } for u in usuarios])
            st.dataframe(df, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("**Acciones sobre usuario**")
        otros = [u for u in usuarios if u.id != usuario_actual.id]
        if otros:
            sel_u = st.selectbox("Seleccionar usuario", otros,
                                 format_func=lambda u: f"{u.username} ({u.rol})"
                                 + (" 🔒 BLOQUEADO" if u.bloqueado_hasta and datetime.utcnow() < u.bloqueado_hasta else ""))
            col1, col2, col3 = st.columns(3)
            with col1:
                lbl = "🔴 Desactivar" if sel_u.activo else "🟢 Activar"
                if st.button(lbl, key="tog_u"):
                    ok, msg = toggle_usuario(session, usuario_actual, sel_u.id)
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                    if ok: st.rerun()
                # Desbloqueo manual si está bloqueado
                if sel_u.bloqueado_hasta and datetime.utcnow() < sel_u.bloqueado_hasta:
                    if st.button("🔓 Desbloquear", key="desbloquear_u"):
                        sel_u.bloqueado_hasta   = None
                        sel_u.intentos_fallidos = 0
                        session.commit()
                        registrar_log(session, usuario_actual, "DESBLOQUEO_USUARIO",
                                      f"Admin desbloqueó a '{sel_u.username}'", "OK")
                        session.commit()
                        alert("success", f"✓ Usuario '{sel_u.username}' desbloqueado")
                        st.rerun()
            with col2:
                nuevo_rol = st.selectbox("Cambiar rol a",
                    [r for r in ["ADMIN","GERENTE","CAJERO","AUDITOR"] if r != sel_u.rol],
                    key="sel_rol")
                if st.button("🔄 Cambiar rol", key="btn_rol"):
                    ok, msg = cambiar_rol(session, usuario_actual, sel_u.id, nuevo_rol)
                    alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                    if ok: st.rerun()
            with col3:
                with st.form("reset_pwd_form"):
                    nueva_pwd = st.text_input("Nueva contraseña (admin reset)",
                                              type="password", placeholder="mín. 6 caracteres")
                    if st.form_submit_button("🔑 Resetear"):
                        ok, msg = cambiar_password(session, usuario_actual, sel_u.id, nueva_pwd)
                        alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)

    with tab2:
        with st.form("nuevo_usuario", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                un  = st.text_input("Nombre de usuario *")
                nm  = st.text_input("Nombre completo *")
            with col2:
                rol = st.selectbox("Rol", ["CAJERO", "GERENTE", "AUDITOR", "ADMIN"])
                pwd = st.text_input("Contraseña *", type="password",
                                    placeholder="mín. 6 caracteres")
            enviado = st.form_submit_button("✅ Crear usuario", type="primary")
        if enviado:
            ok, msg = crear_usuario(session, usuario_actual, un, nm, pwd, rol)
            alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
            if ok: st.rerun()

    with tab3:
        st.markdown(f"Cambiando contraseña de **{usuario_actual.nombre}** (`{usuario_actual.username}`)")
        with st.form("cambio_pwd_self"):
            pwd_actual = st.text_input("Contraseña actual", type="password")
            pwd_nueva  = st.text_input("Nueva contraseña", type="password")
            pwd_conf   = st.text_input("Confirmar nueva contraseña", type="password")
            enviado    = st.form_submit_button("🔑 Cambiar contraseña", type="primary")
        if enviado:
            if pwd_nueva != pwd_conf:
                alert("error", "✗ Las contraseñas nuevas no coinciden.")
            else:
                ok, msg = cambiar_password(session, usuario_actual,
                                           usuario_actual.id, pwd_nueva, pwd_actual)
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)


# ─────────────────────────────
# VISTA: AUDITORÍA
# ─────────────────────────────
def vista_auditoria(session):
    render_header("📋", "Registro de auditoría",
                  "Historial inmutable de todas las acciones del sistema")

    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_accion = st.selectbox("Acción", ["Todas","LOGIN","DEPOSITO","RETIRO",
            "TRANSFERENCIA","PRESTAMO","PAGO_PRESTAMO","CREAR_CLIENTE","EDITAR_CLIENTE",
            "SUSPENDER_CLIENTE","REACTIVAR_CLIENTE","CERRAR_CUENTA",
            "CREAR_USUARIO","TOGGLE_USUARIO","RESET_PASSWORD","CAMBIO_PASSWORD"])
    with col2:
        filtro_resultado = st.selectbox("Resultado", ["Todos","OK","ERROR"])
    with col3:
        limite = st.selectbox("Mostrar", [50, 100, 250, 500], index=0)

    q = session.query(AuditLog).order_by(AuditLog.fecha.desc())
    if filtro_accion != "Todas":
        q = q.filter(AuditLog.accion == filtro_accion)
    if filtro_resultado != "Todos":
        q = q.filter(AuditLog.resultado == filtro_resultado)
    logs = q.limit(limite).all()

    if logs:
        df = pd.DataFrame([{
            "Fecha":    l.fecha.strftime("%Y-%m-%d %H:%M:%S") if l.fecha else "—",
            "Usuario":  l.username,
            "Rol":      l.rol,
            "Acción":   l.accion,
            "Detalle":  l.detalle,
            "Resultado":l.resultado,
        } for l in logs])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"Mostrando {len(logs)} registros")
    else:
        alert("info", "No hay registros de auditoría con ese filtro.")


# ─────────────────────────────
# VISTA: CONFIGURACIÓN DEL BANCO
# ─────────────────────────────

def vista_configuracion(session, usuario):
    render_header("⚙️", "Configuración del banco",
                  "Ajusta tasas y parámetros sin tocar código")

    CAMPOS = [
        ("tasa_deposito",           "Comisión por depósito",           "0.02",    "Ej: 0.02 = 2%",   "porcentaje"),
        ("tasa_transferencia",      "Comisión por transferencia",      "0.01",    "Ej: 0.01 = 1%",   "porcentaje"),
        ("tasa_prestamo",           "Tasa de interés préstamos",       "0.10",    "Ej: 0.10 = 10%",  "porcentaje"),
        ("tasa_mora",               "Tasa de mora mensual",            "0.02",    "Ej: 0.02 = 2%",   "porcentaje"),
        ("alerta_saldo_minimo",     "Saldo mínimo para alerta ($)",    "50.00",   "Ej: 50.00",        "monto"),
        ("alerta_caja_minima",      "Caja mínima para alerta ($)",     "1000.00", "Ej: 1000.00",      "monto"),
        ("alerta_dias_vencimiento", "Días anticipación vencimiento",   "7",       "Ej: 7",            "dias"),
        ("alerta_intentos_login",   "Intentos login antes de alerta",  "3",       "Ej: 3",            "numero"),
        ("limite_retiro_diario",    "Límite diario de retiros ($)",    "5000.00", "0 = sin límite",   "monto"),
        ("limite_transferencia_diaria","Límite diario transferencias ($)","10000.00","0 = sin límite","monto"),
    ]

    # Cargar valores actuales
    def get_val(clave, default):
        r = session.query(ConfigBanco).filter_by(clave=clave).first()
        return r.valor if r else default

    alert("info",
          "Los cambios se aplican <strong>inmediatamente</strong>. "
          "Las tasas son decimales: 0.02 = 2%, 0.10 = 10%.")

    st.markdown("#### Tasas y comisiones")
    with st.form("config_form"):
        nuevos_vals = {}
        tasas  = [c for c in CAMPOS if c[4] == "porcentaje"]
        params = [c for c in CAMPOS if c[4] != "porcentaje"]

        col1, col2 = st.columns(2)
        for i, (clave, label, default, ayuda, _) in enumerate(tasas):
            val_actual = get_val(clave, default)
            col = col1 if i % 2 == 0 else col2
            with col:
                nuevos_vals[clave] = st.number_input(
                    label,
                    value=float(val_actual),
                    min_value=0.0,
                    max_value=1.0,
                    step=0.001,
                    format="%.4f",
                    help=ayuda,
                    key=f"cfg_{clave}",
                )

        st.markdown("#### Parámetros de alertas")
        col3, col4 = st.columns(2)
        for i, (clave, label, default, ayuda, tipo) in enumerate(params):
            val_actual = get_val(clave, default)
            col = col3 if i % 2 == 0 else col4
            with col:
                if tipo == "monto":
                    nuevos_vals[clave] = st.number_input(
                        label, value=float(val_actual), min_value=0.0, step=10.0, help=ayuda, key=f"cfg_{clave}")
                else:
                    nuevos_vals[clave] = st.number_input(
                        label, value=int(float(val_actual)), min_value=1, step=1, help=ayuda, key=f"cfg_{clave}")

        guardado = st.form_submit_button("💾 Guardar configuración", type="primary")

    if guardado:
        try:
            for clave, valor in nuevos_vals.items():
                existing = session.query(ConfigBanco).filter_by(clave=clave).first()
                if existing:
                    existing.valor = str(valor)
                else:
                    session.add(ConfigBanco(clave=clave, valor=str(valor)))
            session.commit()
            registrar_log(session, usuario, "CONFIGURACION",
                          "Tasas y parámetros actualizados", "OK")
            alert("success", "✓ Configuración guardada. Las tasas aplican en la próxima operación.")
            st.rerun()
        except Exception as e:
            session.rollback()
            alert("error", f"✗ Error al guardar: {e}")

    # Mostrar valores actuales
    st.divider()
    st.markdown("#### Valores actuales")
    filas = []
    for clave, label, default, _, tipo in CAMPOS:
        val = get_val(clave, default)
        if tipo == "porcentaje":
            display = f"{float(val)*100:.2f}%"
        elif tipo == "monto":
            display = f"${float(val):,.2f}"
        else:
            display = str(int(float(val)))
        filas.append({"Parámetro": label, "Valor actual": display, "Clave DB": clave})

    df_cfg = pd.DataFrame(filas)
    st.dataframe(df_cfg, use_container_width=True, hide_index=True)

    # ── Gestión de Tipos de Cuenta ──
    st.divider()
    st.markdown("#### 🏦 Tipos de Cuenta")
    tipos = session.query(TipoCuenta).all()
    if tipos:
        data_tc = [{"ID": t.id, "Nombre": t.nombre,
                    "Tasa anual": f"{float(t.tasa_interes)*100:.2f}%",
                    "Saldo mínimo": f"${float(t.saldo_minimo):,.2f}",
                    "Comisión": "Sí" if t.cobra_comision else "No",
                    "Activo": "✅" if t.activo else "❌"} for t in tipos]
        st.dataframe(pd.DataFrame(data_tc), use_container_width=True, hide_index=True)

    with st.expander("➕ Agregar tipo de cuenta"):
        col_tc1, col_tc2 = st.columns(2)
        with col_tc1:
            tc_nombre   = st.text_input("Nombre del tipo", key="tc_nom")
            tc_tasa     = st.number_input("Tasa de interés anual (%)", min_value=0.0, max_value=20.0, value=3.0, step=0.5, key="tc_tasa")
            tc_saldo    = st.number_input("Saldo mínimo ($)", min_value=0.0, value=0.0, step=10.0, key="tc_saldo")
        with col_tc2:
            tc_comision = st.checkbox("Cobra comisión mensual", key="tc_com")
            tc_desc     = st.text_area("Descripción", height=80, key="tc_desc")
        if st.button("Crear tipo de cuenta", key="btn_create_tc"):
            from extras import crear_tipo_cuenta
            ok, result = crear_tipo_cuenta(session, tc_nombre, tc_tasa/100, tc_saldo, tc_comision, tc_desc)
            if ok:
                session.commit()
                st.success(f"✅ Tipo '{result.nombre}' creado")
                st.rerun()
            else:
                st.error(result)

    # ── Gestión de Sucursales ──
    st.divider()
    st.markdown("#### 🏢 Sucursales")
    suc_list = session.query(Sucursal).order_by(Sucursal.nombre).all()
    if suc_list:
        data_s = [{"Nombre": s.nombre, "Dirección": s.direccion,
                   "Teléfono": s.telefono or "—", "Caja": f"${float(s.saldo_caja):,.2f}",
                   "Activa": "✅" if s.activa else "❌"} for s in suc_list]
        st.dataframe(pd.DataFrame(data_s), use_container_width=True, hide_index=True)


# ─────────────────────────────
# VISTA: ALERTAS DEL SISTEMA
# ─────────────────────────────

def vista_alertas(session):
    render_header("🔔", "Alertas del sistema",
                  "Monitoreo automático de eventos críticos")

    caja = caja_real(session)
    todas = obtener_todas_alertas(session, caja)

    col_a, col_b, col_c, col_d = st.columns(4)
    errores  = [a for a in todas if a["nivel"] == NIVEL_ERROR]
    warnings = [a for a in todas if a["nivel"] == NIVEL_WARNING]
    infos    = [a for a in todas if a["nivel"] == NIVEL_INFO]

    with col_a: render_metric("Total alertas",   str(len(todas)))
    with col_b: render_metric("🔴 Críticas",     str(len(errores)))
    with col_c: render_metric("🟡 Advertencias", str(len(warnings)))
    with col_d: render_metric("🔵 Info",         str(len(infos)))

    st.divider()

    col_acc1, col_acc2 = st.columns(2)
    with col_acc1:
        if st.button("🔄 Recalcular mora de préstamos", type="secondary"):
            n, msg = calcular_mora(session)
            alert("success" if n >= 0 else "error", f"✓ {msg}")
            st.rerun()
    with col_acc2:
        if st.button("🔄 Actualizar alertas", type="secondary"):
            st.rerun()

    st.divider()

    if not todas:
        alert("success", "✅ No hay alertas activas. El sistema opera con normalidad.")
        return

    ICONO_NIVEL = {NIVEL_ERROR: "🔴", NIVEL_WARNING: "🟡", NIVEL_INFO: "🔵"}
    COLOR_NIVEL = {NIVEL_ERROR: "error", NIVEL_WARNING: "warning", NIVEL_INFO: "info"}

    for a in todas:
        icono = ICONO_NIVEL.get(a["nivel"], "⚪")
        nivel_css = COLOR_NIVEL.get(a["nivel"], "info")
        alert(nivel_css,
              f"{icono} <strong>{a['titulo']}</strong><br/>"
              f"<span style='font-size:.85rem'>{a['detalle']}</span>"
              f"<span style='float:right;font-size:.75rem;opacity:.6'>{a['ts']}</span>")


# ─────────────────────────────
# VISTA: REPORTES — Mejorada con historial y cartera
# ─────────────────────────────

def _vista_reportes_extra(session, inicio, fin):
    """Tabs adicionales: balance histórico, cartera vencida."""
    tab5, tab6 = st.tabs(["📉 Balance histórico", "💀 Cartera vencida"])

    # ── Balance histórico mensual ──
    with tab5:
        st.markdown("#### Evolución mensual del balance")
        # Construir datos por mes desde asientos
        from sqlalchemy import extract
        asientos = (session.query(
                        extract("year",  Asiento.fecha).label("anio"),
                        extract("month", Asiento.fecha).label("mes"),
                        func.sum(LineaAsiento.credito).label("creditos"),
                        func.sum(LineaAsiento.debito).label("debitos"),
                    )
                    .join(LineaAsiento)
                    .filter(Asiento.fecha >= inicio, Asiento.fecha <= fin)
                    .group_by("anio", "mes")
                    .order_by("anio", "mes")
                    .all())
        if asientos:
            df_hist = pd.DataFrame([{
                "Período":   f"{int(r.anio)}-{int(r.mes):02d}",
                "Créditos":  float(r.creditos or 0),
                "Débitos":   float(r.debitos or 0),
                "Diferencia":float(r.creditos or 0) - float(r.debitos or 0),
            } for r in asientos])

            fig_hist = px.line(
                df_hist, x="Período", y=["Créditos", "Débitos"],
                color_discrete_sequence=[COLORES[0], COLORES[3]],
                markers=True,
            )
            fig_hist.update_layout(
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                height=300, margin=dict(l=0,r=0,t=20,b=0),
                yaxis=dict(tickprefix="$"), legend=dict(orientation="h"),
            )
            st.plotly_chart(fig_hist, use_container_width=True)
            st.dataframe(df_hist, use_container_width=True, hide_index=True)

            # Botón de descarga CSV
            csv_bal = generar_balance_csv([
                {"nombre": r["Período"], "categoria": "HISTÓRICO",
                 "saldo":  r["Créditos"]}
                for _, r in df_hist.iterrows()
            ])
            st.download_button(
                "📥 Descargar balance CSV",
                data=csv_bal,
                file_name=f"balance_historico_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )
        else:
            alert("info", "No hay datos en el período seleccionado.")

    # ── Cartera vencida ──
    with tab6:
        st.markdown("#### Cartera vencida y tasa de morosidad")
        hoy = datetime.now().date()

        todos_prestamos  = (
            session.query(Prestamo)
            .options(selectinload(Prestamo.cliente))
            .filter(Prestamo.estado == "ACTIVO")
            .all()
        )
        prestamos_vencidos = [p for p in todos_prestamos
                               if p.fecha_vencimiento and p.fecha_vencimiento < hoy
                               and float(p.saldo_pendiente) > 0]

        total_cartera = sum(float(p.saldo_pendiente) for p in todos_prestamos)
        cartera_vencida = sum(float(p.saldo_pendiente) for p in prestamos_vencidos)
        tasa_morosidad = (cartera_vencida / total_cartera * 100) if total_cartera > 0 else 0
        provision = cartera_vencida * 0.20  # Provisión estándar 20%

        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a: render_metric("Cartera total",    f"${total_cartera:,.2f}")
        with col_b: render_metric("Cartera vencida",  f"${cartera_vencida:,.2f}")
        with col_c: render_metric("Tasa morosidad",   f"{tasa_morosidad:.1f}%")
        with col_d: render_metric("Provisión (20%)",  f"${provision:,.2f}")

        if tasa_morosidad > 5:
            alert("error", f"⚠️ Tasa de morosidad crítica: {tasa_morosidad:.1f}% — Se recomienda acción inmediata.")
        elif tasa_morosidad > 2:
            alert("warning", f"🟡 Tasa de morosidad elevada: {tasa_morosidad:.1f}%")
        elif total_cartera > 0:
            alert("success", f"✅ Cartera saludable — morosidad: {tasa_morosidad:.1f}%")

        if prestamos_vencidos:
            st.markdown("#### Detalle cartera vencida")
            df_cv = pd.DataFrame([{
                "Cliente":        p.cliente.nombre,
                "Préstamo #":     p.id,
                "Vencimiento":    str(p.fecha_vencimiento),
                "Días mora":      (hoy - p.fecha_vencimiento).days,
                "Pendiente ($)":  f"${float(p.saldo_pendiente):,.2f}",
                "Mora ($)":       f"${float(p.mora_acumulada or 0):,.2f}",
            } for p in sorted(prestamos_vencidos, key=lambda x: x.fecha_vencimiento)])
            st.dataframe(df_cv, use_container_width=True, hide_index=True)
        else:
            alert("success", "✅ No hay préstamos vencidos en cartera.")


# ─────────────────────────────
# EXPORTACIONES EN VISTAS EXISTENTES
# ─────────────────────────────

def _botones_exportar_historial(session, cliente, movimientos):
    """Añade botones de exportación al historial del cliente."""
    if not movimientos:
        return
    st.markdown("#### Exportar")
    col1, col2 = st.columns(2)
    with col1:
        pdf_bytes = generar_estado_cuenta_pdf(cliente, movimientos)
        st.download_button(
            "📄 Estado de cuenta PDF",
            data=pdf_bytes,
            file_name=f"estado_{cliente.num_cuenta}_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            key=f"dl_pdf_{cliente.id}",
        )
    with col2:
        csv_bytes = generar_movimientos_csv(movimientos, cliente)
        st.download_button(
            "📊 Movimientos CSV",
            data=csv_bytes,
            file_name=f"movimientos_{cliente.num_cuenta}_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key=f"dl_csv_{cliente.id}",
        )


def _boton_exportar_amortizacion(session, prestamo, cliente):
    """Botón para descargar tabla de amortización."""
    cuotas = sorted(prestamo.cuotas, key=lambda c: c.numero_cuota)
    pdf_bytes = generar_amortizacion_pdf(prestamo, cuotas, cliente)
    st.download_button(
        "📄 Tabla de amortización PDF",
        data=pdf_bytes,
        file_name=f"amortizacion_prestamo{prestamo.id}_{datetime.now().strftime('%Y%m%d')}.pdf",
        mime="application/pdf",
        key=f"dl_amort_{prestamo.id}",
    )


# ─────────────────────────────
# BADGE DE ALERTAS EN SIDEBAR
# ─────────────────────────────

def _render_alertas_badge(session):
    """Muestra un mini-resumen de alertas en el sidebar."""
    try:
        caja = caja_real(session)
        cnt = contar_alertas(session, caja)
        if cnt["total"] > 0:
            color_badge = "#C87E7E" if cnt["error"] > 0 else "#C8A87E"
            st.markdown(
                f"<div style='background:rgba(200,126,126,.1);border:1px solid {color_badge};"
                f"border-radius:8px;padding:8px 12px;margin:8px 0;font-size:.82rem'>"
                f"🔔 <b>{cnt['total']} alerta(s)</b>"
                f"{'  🔴 ' + str(cnt['error']) if cnt['error'] else ''}"
                f"{'  🟡 ' + str(cnt['warning']) if cnt['warning'] else ''}"
                f"</div>",
                unsafe_allow_html=True
            )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
#  VISTA: TARJETAS
# ═══════════════════════════════════════════════════════════

def vista_tarjetas(session, usuario):
    render_header("💳", "Tarjetas", "Débito y crédito")
    tab1, tab2 = st.tabs(["Tarjetas de Débito", "Tarjetas de Crédito"])

    with tab1:
        st.subheader("Tarjetas de Débito")
        clientes = session.query(Cliente).filter(Cliente.estado == "ACTIVO").order_by(Cliente.nombre).all()
        if tiene_permiso(usuario, "gestionar_tarjetas"):
            with st.expander("➕ Emitir Tarjeta de Débito"):
                c_sel = st.selectbox("Cliente", clientes, format_func=lambda c: f"{c.nombre} ({c.num_cuenta})", key="td_cliente")
                if st.button("Emitir", key="btn_td"):
                    ok, result = emitir_tarjeta_debito(session, c_sel.id)
                    if ok:
                        session.commit()
                        st.success(f"✅ Tarjeta emitida: {result.numero[:4]}...{result.numero[-4:]}")
                    else:
                        st.error(result)

        tarjetas = session.query(TarjetaDebito).all()
        if tarjetas:
            data = []
            for t in tarjetas:
                c = session.query(Cliente).filter_by(id=t.cliente_id).first()
                data.append({"Cliente": c.nombre if c else "—", "Número": f"****{t.numero[-4:]}",
                              "Vencimiento": t.vencimiento, "Estado": t.estado,
                              "Emitida": t.creado_en.strftime("%Y-%m-%d") if t.creado_en else "—"})
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            st.info("No hay tarjetas de débito registradas.")

    with tab2:
        st.subheader("Tarjetas de Crédito")
        if tiene_permiso(usuario, "gestionar_tarjetas"):
            with st.expander("➕ Emitir Tarjeta de Crédito"):
                c_sel2 = st.selectbox("Cliente", clientes, format_func=lambda c: f"{c.nombre} (Score: {c.score_credito})", key="tc_cliente")
                limite = st.number_input("Límite de crédito ($)", min_value=100.0, value=500.0, step=100.0)
                if st.button("Emitir", key="btn_tc"):
                    ok, result = emitir_tarjeta_credito(session, c_sel2.id, limite)
                    if ok:
                        session.commit()
                        st.success(f"✅ Tarjeta crédito emitida: {result.numero[:4]}...{result.numero[-4:]} | Límite: ${float(result.limite):,.2f}")
                    else:
                        st.error(result)

            with st.expander("💳 Registrar Consumo (Manual)"):
                tarjetas_activas = session.query(TarjetaCredito).filter_by(estado="ACTIVA").all()
                if tarjetas_activas:
                    t_sel = st.selectbox(
                        "Seleccionar Tarjeta", 
                        tarjetas_activas, 
                        format_func=lambda t: f"****{t.numero[-4:]} - Límite: ${float(t.limite):,.2f}"
                    )
                    monto_consumo = st.number_input("Monto del Consumo ($)", min_value=1.0, value=50.0, step=10.0)
                    
                    if st.button("🚀 Procesar Consumo", type="primary"):
                        try:
                            from operaciones import procesar_consumo_tarjeta
                            tarjeta_act = procesar_consumo_tarjeta(session, t_sel.id, monto_consumo)
                            session.commit()
                            st.success(f"✅ Consumo autorizado. Nuevo saldo usado: ${tarjeta_act.saldo_usado:,.2f}")
                            st.rerun()
                        except Exception as e:
                            session.rollback()
                            st.error(f"❌ Error al procesar: {str(e)}")
                else:
                    st.info("No hay tarjetas de crédito activas en el sistema.")

        tarjetas_c = session.query(TarjetaCredito).all()
        if tarjetas_c:
            data = []
            for t in tarjetas_c:
                c = session.query(Cliente).filter_by(id=t.cliente_id).first()
                disponible = float(t.limite) - float(t.saldo_usado)
                data.append({"Cliente": c.nombre if c else "—",
                              "Número": f"****{t.numero[-4:]}",
                              "Límite": f"${float(t.limite):,.2f}",
                              "Usado": f"${float(t.saldo_usado):,.2f}",
                              "Disponible": f"${disponible:,.2f}",
                              "Corte día": t.fecha_corte,
                              "Estado": t.estado})
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            st.info("No hay tarjetas de crédito registradas.")


# ═══════════════════════════════════════════════════════════
#  VISTA: SUCURSALES & ATM
# ═══════════════════════════════════════════════════════════

def vista_sucursales(session, usuario):
    render_header("🏦", "Sucursales & ATM", "Red de atención")
    tab1, tab2 = st.tabs(["Sucursales", "Cajeros ATM"])

    with tab1:
        sucursales = session.query(Sucursal).order_by(Sucursal.nombre).all()
        if tiene_permiso(usuario, "gestionar_sucursales"):
            with st.expander("➕ Nueva Sucursal"):
                nombre_s = st.text_input("Nombre", key="suc_nom")
                dir_s    = st.text_input("Dirección", key="suc_dir")
                tel_s    = st.text_input("Teléfono", key="suc_tel")
                if st.button("Crear Sucursal"):
                    from extras import crear_sucursal
                    ok, result = crear_sucursal(session, nombre_s, dir_s, tel_s)
                    if ok:
                        session.commit()
                        st.success(f"✅ Sucursal {result.nombre} creada")
                        st.rerun()
                    else:
                        st.error(result)

        cols = st.columns(2)
        for i, s in enumerate(sucursales):
            cnt_clientes = session.query(func.count(Cliente.id)).filter_by(sucursal_id=s.id).scalar() or 0
            cnt_atm = session.query(func.count(ATM.id)).filter_by(sucursal_id=s.id).scalar() or 0
            with cols[i % 2]:
                estado_color = "#2ecc71" if s.activa else "#e74c3c"
                st.markdown(f"""
<div style="background:#1e1e2e;border:1px solid #3d3b3c;border-radius:10px;padding:16px;margin-bottom:12px">
  <div style="font-size:1.1rem;font-weight:700;color:#e8e4d9">{s.nombre}</div>
  <div style="color:#8a8694;font-size:.82rem">{s.direccion or "—"}</div>
  <div style="margin-top:8px;display:flex;gap:12px">
    <span style="color:#7ec8c8">👥 {cnt_clientes} clientes</span>
    <span style="color:#a89cc8">🏧 {cnt_atm} ATM(s)</span>
    <span style="color:{estado_color}">● {"Activa" if s.activa else "Inactiva"}</span>
  </div>
</div>""", unsafe_allow_html=True)

    with tab2:
        atms = session.query(ATM).all()
        if atms:
            data = []
            for a in atms:
                suc = session.query(Sucursal).filter_by(id=a.sucursal_id).first()
                data.append({"Sucursal": suc.nombre if suc else "—",
                              "Ubicación": a.ubicacion,
                              "Estado": a.estado,
                              "Saldo ATM": f"${float(a.saldo_atm):,.2f}"})
            st.dataframe(pd.DataFrame(data), use_container_width=True)
        else:
            st.info("No hay ATMs registrados.")


# ═══════════════════════════════════════════════════════════
#  VISTA: PLAZO FIJO
# ═══════════════════════════════════════════════════════════

def vista_plazo_fijo(session, usuario):
    from datetime import date
    render_header("📑", "Depósitos a Plazo Fijo", "Certificados de depósito")
    tab1, tab2 = st.tabs(["Certificados Activos", "Nuevo Depósito"])

    with tab2:
        if tiene_permiso(usuario, "gestionar_plazo_fijo"):
            clientes = session.query(Cliente).filter(Cliente.estado == "ACTIVO").order_by(Cliente.nombre).all()
            c_sel = st.selectbox("Cliente", clientes, format_func=lambda c: f"{c.nombre} | Saldo: ${float(c.saldo):,.2f}")
            col1, col2 = st.columns(2)
            with col1:
                monto  = st.number_input("Monto ($)", min_value=500.0, value=1000.0, step=500.0)
                plazo  = st.selectbox("Plazo (meses)", [3, 6, 12, 18, 24, 36, 48, 60])
            with col2:
                tasa   = st.number_input("Tasa anual (%)", min_value=1.0, max_value=15.0, value=5.0, step=0.5) / 100
                renov  = st.checkbox("Renovación automática")
            interes_proy = monto * tasa * plazo / 12
            st.info(f"📊 Interés proyectado: **${interes_proy:,.2f}** | Total al vencimiento: **${monto + interes_proy:,.2f}**")
            if st.button("✅ Crear Depósito", type="primary"):
                try:
                    from operaciones import procesar_apertura_plazo_fijo
                    dpf = procesar_apertura_plazo_fijo(session, c_sel.id, monto, tasa, plazo)
                    session.commit()
                    st.success(f"✅ Certificado de plazo fijo creado contablemente con éxito.")
                    st.rerun()
                except Exception as e:
                    session.rollback()
                    st.error(f"❌ Transacción denegada: {str(e)}")

    with tab1:
        depositos = session.query(DepositoPlazoFijo).order_by(DepositoPlazoFijo.fecha_apertura.desc()).all()
        if depositos:
            hoy = date.today()
            data = []
            for d in depositos:
                c = session.query(Cliente).filter_by(id=d.cliente_id).first()
                dias_rest = (d.fecha_vencimiento - hoy).days if d.fecha_vencimiento else 0
                data.append({
                    "Certificado": d.num_certificado,
                    "Cliente": c.nombre if c else "—",
                    "Monto": f"${float(d.monto):,.2f}",
                    "Tasa": f"{float(d.tasa_anual)*100:.1f}%",
                    "Plazo": f"{d.plazo_meses} meses",
                    "Vencimiento": str(d.fecha_vencimiento),
                    "Total": f"${float(d.monto_total or 0):,.2f}",
                    "Estado": d.estado,
                    "Días rest.": dias_rest,
                })
            st.dataframe(pd.DataFrame(data), use_container_width=True)

            if tiene_permiso(usuario, "gestionar_plazo_fijo"):
                activos = [d for d in depositos if d.estado == "ACTIVO"]
                vencidos = [d for d in activos if d.fecha_vencimiento and d.fecha_vencimiento <= hoy]

                if vencidos:
                    st.warning(f"⚠️ {len(vencidos)} depósito(s) vencido(s) pendientes de pago.")

                if activos:
                    st.markdown("---")
                    st.markdown("#### 💰 Liquidar Depósito a Plazo Fijo")
                    dpf_sel = st.selectbox(
                        "Seleccionar certificado a liquidar",
                        activos,
                        format_func=lambda d: (
                            f"{d.num_certificado} — ${float(d.monto_total):,.2f} "
                            f"({'VENCIDO' if d.fecha_vencimiento and d.fecha_vencimiento <= hoy else f'vence {d.fecha_vencimiento}'})"
                        ),
                        key="dpf_liquidar_sel"
                    )
                    if dpf_sel:
                        es_vencido = dpf_sel.fecha_vencimiento and dpf_sel.fecha_vencimiento <= hoy
                        cliente_dpf = session.query(Cliente).filter_by(id=dpf_sel.cliente_id).first()
                        col_ia, col_ib = st.columns(2)
                        with col_ia:
                            st.info(
                                f"**Cliente:** {cliente_dpf.nombre if cliente_dpf else '—'}  \n"
                                f"**Capital:** ${float(dpf_sel.monto):,.2f}  \n"
                                f"**Interés proyectado:** ${float(dpf_sel.interes_proyectado):,.2f}  \n"
                                f"**Total a pagar:** ${float(dpf_sel.monto_total):,.2f}  \n"
                                f"**Estado:** {'✅ VENCIDO — listo para pagar' if es_vencido else '⏳ Vigente — liquidación anticipada'}"
                            )
                        with col_ib:
                            if not es_vencido:
                                st.warning("⚠️ Este certificado aún no ha vencido. Al liquidarlo anticipadamente el cliente recibirá el capital + intereses completos.")
                            lbl = "💰 Pagar al cliente (vencido)" if es_vencido else "💰 Liquidar anticipadamente"
                            if st.button(lbl, type="primary", key="btn_liquidar_dpf"):
                                ok, result = vencer_deposito_plazo(session, dpf_sel.id, usuario.username)
                                if ok:
                                    session.commit()
                                    st.success(f"✅ ${float(result.monto_total):,.2f} acreditados al cliente exitosamente.")
                                    st.rerun()
                                else:
                                    st.error(result)
        else:
            st.info("No hay depósitos a plazo fijo registrados.")


# ═══════════════════════════════════════════════════════════
#  VISTA: SOCIOS
# ═══════════════════════════════════════════════════════════

def vista_socios(session, usuario):
    render_header("🤝", "Socios", "Caja de Crédito — Capital Social")
    tab1, tab2, tab3 = st.tabs(["Lista de Socios", "Registrar Socio", "Aportes"])

    with tab1:
        socios = session.query(Socio).all()
        if socios:
            data = []
            for s in socios:
                c = session.query(Cliente).filter_by(id=s.cliente_id).first()
                data.append({"#": s.numero_socio, "Nombre": c.nombre if c else "—",
                              "Estado": s.estado, "Aporte Total": f"${float(s.aporte_total):,.2f}",
                              "Ingreso": str(s.fecha_ingreso)})
            st.dataframe(pd.DataFrame(data), use_container_width=True)
            total_capital = sum(float(s.aporte_total) for s in socios)
            st.metric("Capital Social Total", f"${total_capital:,.2f}")
        else:
            st.info("No hay socios registrados.")

    with tab2:
        if tiene_permiso(usuario, "gestionar_socios"):
            clientes = session.query(Cliente).filter(Cliente.estado == "ACTIVO").order_by(Cliente.nombre).all()
            ids_socios = {s.cliente_id for s in session.query(Socio).all()}
            disponibles = [c for c in clientes if c.id not in ids_socios]
            if disponibles:
                c_sel = st.selectbox("Cliente", disponibles, format_func=lambda c: c.nombre)
                aporte_ini = st.number_input("Aporte inicial ($)", min_value=0.0, value=100.0, step=50.0)
                if st.button("✅ Registrar Socio", type="primary"):
                    ok, result = registrar_socio(session, c_sel.id, aporte_ini)
                    if ok:
                        session.commit()
                        st.success(f"✅ Socio registrado: {result.numero_socio}")
                        st.rerun()
                    else:
                        st.error(result)
            else:
                st.info("Todos los clientes activos ya son socios.")

    with tab3:
        if tiene_permiso(usuario, "gestionar_socios"):
            socios_list = session.query(Socio).filter_by(estado="ACTIVO").all()
            if socios_list:
                def _nombre_socio(s):
                    c = session.query(Cliente).filter_by(id=s.cliente_id).first()
                    return f"{s.numero_socio} — {c.nombre if c else '—'}"
                socio_sel = st.selectbox("Socio", socios_list, format_func=_nombre_socio)
                monto_a = st.number_input("Monto del aporte ($)", min_value=1.0, value=50.0, step=10.0)
                tipo_a  = st.selectbox("Tipo", ["ORDINARIO", "EXTRAORDINARIO"])
                desc_a  = st.text_input("Descripción", "Aporte mensual")
                if st.button("💰 Registrar Aporte", type="primary"):
                    ok, result = registrar_aporte(session, socio_sel.id, monto_a, tipo_a, desc_a)
                    if ok:
                        session.commit()
                        st.success(f"✅ Aporte de ${monto_a:,.2f} registrado")
                    else:
                        st.error(result)


# ═══════════════════════════════════════════════════════════
#  VISTA: CONTABILIDAD AVANZADA (nuevas cuentas del plan)
# ═══════════════════════════════════════════════════════════

def vista_contabilidad_avanzada(session, usuario):
    render_header("🏦", "Contabilidad Avanzada", "Gestión completa del plan de cuentas bancario")

    if not tiene_permiso(usuario, "reportes"):
        st.error("🔒 Acceso denegado")
        return

    # Helper local: siempre pasa el objeto usuario completo a registrar_log
    def _audit(accion, detalle, resultado="OK"):
        audit(session, usuario, accion, detalle, resultado)

    tabs = st.tabs([
        "💰 Inversiones",
        "🏢 Bienes e Inmuebles",
        "⚠️ Mora y Provisiones",
        "🏛️ Pasivos Bancarios",
        "🧾 Impuestos",
        "📊 Patrimonio y Reservas",
        "💳 Comisiones TC",
        "⚡ Gastos Operativos",
        "📋 Saldos del Plan",
    ])

    # ── Tab 0: INVERSIONES ──────────────────────────────────────────
    with tabs[0]:
        st.subheader("Gestión de Inversiones")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Registrar nueva inversión**")
            inv_monto = st.number_input("Monto a invertir ($)", min_value=100.0, value=1000.0, step=100.0, key="inv_monto")
            inv_desc  = st.text_input("Descripción", value="Inversión en bonos del Estado", key="inv_desc")
            if st.button("💸 Registrar Inversión", key="btn_inv"):
                try:
                    ok, msg = registrar_inversion(session, inv_monto, inv_desc)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("INVERSION_REGISTRAR", inv_desc, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

        with col2:
            st.markdown("**Liquidar inversión**")
            liq_monto   = st.number_input("Capital a recuperar ($)", min_value=100.0, value=1000.0, step=100.0, key="liq_monto")
            liq_ganancia = st.number_input("Ganancia ($, opcional)", min_value=0.0, value=0.0, step=10.0, key="liq_gan")
            liq_desc    = st.text_input("Descripción", value="Liquidación de inversión", key="liq_desc")
            if st.button("💹 Liquidar Inversión", key="btn_liq"):
                try:
                    ok, msg = liquidar_inversion(session, liq_monto, liq_ganancia, liq_desc)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("INVERSION_LIQUIDAR", liq_desc, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

        from contabilidad import saldo_cuenta as sc
        saldo_inv = sc(session, "Inversiones")
        st.metric("Saldo actual — Inversiones", f"${saldo_inv:,.2f}")

    # ── Tab 1: BIENES E INMUEBLES ────────────────────────────────────
    with tabs[1]:
        st.subheader("Bienes e Inmuebles")
        st.info("Registra edificios, equipos bancarios, mobiliario y otros activos fijos.")
        bien_monto = st.number_input("Valor del bien ($)", min_value=100.0, value=5000.0, step=500.0, key="bien_monto")
        bien_desc  = st.text_input("Descripción del bien", value="Equipo de cómputo", key="bien_desc")
        if st.button("🏢 Adquirir Bien/Inmueble", key="btn_bien"):
            try:
                ok, msg = adquirir_bien_inmueble(session, bien_monto, bien_desc)
                session.commit()
                alert("success" if ok else "error", msg)
                _audit("BIEN_ADQUIRIR", bien_desc, "OK" if ok else "ERROR")
            except Exception as e:
                session.rollback(); alert("error", str(e))
        from contabilidad import saldo_cuenta as sc
        st.metric("Saldo — Bienes e Inmuebles", f"${sc(session, 'Bienes e Inmuebles'):,.2f}")

    # ── Tab 2: MORA Y PROVISIONES ────────────────────────────────────
    with tabs[2]:
        st.subheader("Mora y Provisiones para Incobrables")
        from models import Prestamo as P
        prestamos_activos = session.query(P).filter(P.estado == "ACTIVO", P.dias_mora > 0).all()

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Clasificar préstamo como moroso**")
            if prestamos_activos:
                opts = {f"#{p.id} — ${p.saldo_pendiente:,.2f} ({p.dias_mora} días mora)": p.id
                        for p in prestamos_activos}
                sel = st.selectbox("Selecciona préstamo", list(opts.keys()), key="sel_moroso")
                if st.button("⚠️ Clasificar Moroso", key="btn_moroso"):
                    try:
                        ok, msg = clasificar_prestamo_moroso(session, opts[sel])
                        session.commit()
                        alert("success" if ok else "error", msg)
                        _audit("PRESTAMO_MOROSO", msg, "OK" if ok else "ERROR")
                    except Exception as e:
                        session.rollback(); alert("error", str(e))
            else:
                st.info("No hay préstamos activos con mora registrada.")

            st.markdown("---")
            st.markdown("**Constituir provisión**")
            prov_monto = st.number_input("Monto provisión ($)", min_value=50.0, value=500.0, step=50.0, key="prov_monto")
            prov_desc  = st.text_input("Descripción", value="Provisión Q1", key="prov_desc")
            if st.button("🛡️ Constituir Provisión", key="btn_prov"):
                try:
                    ok, msg = constituir_provision(session, prov_monto, prov_desc)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("PROVISION_CONSTITUIR", prov_desc, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

        with col2:
            st.markdown("**Castigar préstamo incobrable**")
            morosos = session.query(P).filter(P.estado == "MOROSO").all()
            if morosos:
                opts_m = {f"#{p.id} — ${p.saldo_pendiente:,.2f}": p.id for p in morosos}
                sel_m = st.selectbox("Selecciona préstamo moroso", list(opts_m.keys()), key="sel_cast")
                if st.button("💀 Castigar (Dar de Baja)", key="btn_cast"):
                    try:
                        ok, msg = castigar_prestamo_incobrable(session, opts_m[sel_m])
                        session.commit()
                        alert("success" if ok else "error", msg)
                        _audit("PRESTAMO_CASTIGAR", msg, "OK" if ok else "ERROR")
                    except Exception as e:
                        session.rollback(); alert("error", str(e))
            else:
                st.info("No hay préstamos morosos para castigar.")

            from contabilidad import saldo_cuenta as sc
            st.metric("Provisión Acumulada", f"${sc(session, 'Provision Incobrables'):,.2f}")
            st.metric("Préstamos Morosos", f"${sc(session, 'Prestamos Morosos'):,.2f}")

        st.markdown("---")
        st.markdown("**Cobrar mora a cliente**")
        from models import Cliente as Cli
        clientes = session.query(Cli).filter(Cli.estado == "ACTIVO").all()
        col3, col4 = st.columns(2)
        with col3:
            cli_opts = {f"{c.nombre} (#{c.id})": c.id for c in clientes}
            cli_sel = st.selectbox("Cliente", list(cli_opts.keys()), key="mora_cli")
        with col4:
            mora_monto = st.number_input("Monto mora ($)", min_value=1.0, value=10.0, step=5.0, key="mora_monto")
        if st.button("💰 Cobrar Mora", key="btn_mora_cobrar"):
            try:
                ok, msg = cobrar_mora_prestamo(session, cli_opts[cli_sel], mora_monto)
                session.commit()
                alert("success" if ok else "error", msg)
                _audit("MORA_COBRAR", msg, "OK" if ok else "ERROR")
            except Exception as e:
                session.rollback(); alert("error", str(e))

    # ── Tab 3: PASIVOS BANCARIOS ────────────────────────────────────
    with tabs[3]:
        st.subheader("Obligaciones con Bancos")
        st.info("Gestiona fondeo interbancario: préstamos recibidos de otros bancos.")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Recibir préstamo de otro banco**")
            pb_monto  = st.number_input("Monto ($)", min_value=1000.0, value=10000.0, step=1000.0, key="pb_monto")
            pb_banco  = st.text_input("Nombre del banco prestamista", value="Banco Agrícola", key="pb_banco")
            if st.button("🏛️ Registrar Fondeo", key="btn_pb"):
                try:
                    ok, msg = recibir_prestamo_banco(session, pb_monto, pb_banco)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("FONDEO_RECIBIR", msg, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))
        with col2:
            st.markdown("**Pagar cuota interbancaria**")
            pp_capital  = st.number_input("Capital ($)", min_value=500.0, value=5000.0, step=500.0, key="pp_cap")
            pp_interes  = st.number_input("Interés ($)", min_value=0.0, value=0.0, step=50.0, key="pp_int")
            pp_banco    = st.text_input("Banco acreedor", value="Banco Agrícola", key="pp_banco")
            if st.button("💳 Registrar Pago Interbancario", key="btn_pp"):
                try:
                    ok, msg = pagar_obligacion_banco(session, pp_capital, pp_interes, pp_banco)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("FONDEO_PAGAR", msg, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

        from contabilidad import saldo_cuenta as sc
        st.metric("Saldo — Obligaciones con Bancos", f"${sc(session, 'Obligaciones con Bancos'):,.2f}")

        st.markdown("---")
        st.subheader("Depósitos por Tipo de Cuenta")
        col3, col4 = st.columns(2)
        with col3:
            st.metric("Cuentas de Ahorro", f"${sc(session, 'Cuentas de Ahorro'):,.2f}")
        with col4:
            st.metric("Cuentas Corrientes", f"${sc(session, 'Cuentas Corrientes'):,.2f}")
        st.caption("Usa las operaciones principales de Depósito para mover fondos a estas cuentas.")
        st.markdown("**Depósito directo a cuenta de ahorro**")
        from models import Cliente as Cli2
        cl2 = session.query(Cli2).filter(Cli2.estado == "ACTIVO").all()
        col5, col6 = st.columns(2)
        with col5:
            opts_ah = {f"{c.nombre} (#{c.id})": c.id for c in cl2}
            sel_ah = st.selectbox("Cliente", list(opts_ah.keys()), key="ah_cli")
            tipo_dep = st.radio("Tipo de cuenta", ["Ahorro", "Corriente"], key="tipo_dep")
        with col6:
            monto_ah = st.number_input("Monto ($)", min_value=10.0, value=500.0, step=50.0, key="ah_monto")
        if st.button("💾 Registrar Depósito Tipificado", key="btn_ah"):
            try:
                if tipo_dep == "Ahorro":
                    ok, msg = deposito_cuenta_ahorro(session, opts_ah[sel_ah], monto_ah)
                else:
                    ok, msg = deposito_cuenta_corriente(session, opts_ah[sel_ah], monto_ah)
                session.commit()
                alert("success" if ok else "error", msg)
                audit(session, usuario,
                      f"DEP_{tipo_dep.upper()}", msg, "OK" if ok else "ERROR")
            except Exception as e:
                session.rollback(); alert("error", str(e))

    # ── Tab 4: IMPUESTOS ────────────────────────────────────────────
    with tabs[4]:
        st.subheader("Gestión de Impuestos")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Provisionar impuesto**")
            imp_monto = st.number_input("Monto ($)", min_value=10.0, value=100.0, step=50.0, key="imp_monto")
            imp_tipo  = st.selectbox("Tipo de impuesto", ["Renta", "IVA", "Municipal", "Otro"], key="imp_tipo")
            if st.button("📋 Provisionar", key="btn_imp_prov"):
                try:
                    ok, msg = provisionar_impuesto(session, imp_monto, imp_tipo)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("IMPUESTO_PROVISIONAR", f"{imp_tipo} ${imp_monto}", "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))
        with col2:
            st.markdown("**Pagar impuesto**")
            pag_monto = st.number_input("Monto a pagar ($)", min_value=10.0, value=100.0, step=50.0, key="pag_imp_monto")
            pag_tipo  = st.selectbox("Tipo", ["Renta", "IVA", "Municipal", "Otro"], key="pag_imp_tipo")
            if st.button("💸 Pagar Impuesto", key="btn_imp_pagar"):
                try:
                    ok, msg = pagar_impuesto(session, pag_monto, pag_tipo)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("IMPUESTO_PAGAR", f"{pag_tipo} ${pag_monto}", "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

        from contabilidad import saldo_cuenta as sc
        st.metric("Impuestos por Pagar (pendientes)", f"${sc(session, 'Impuestos por Pagar'):,.2f}")

    # ── Tab 5: PATRIMONIO Y RESERVAS ────────────────────────────────
    with tabs[5]:
        st.subheader("Patrimonio — Reservas Legales y Utilidades")
        from contabilidad import saldo_cuenta as sc
        col1, col2, col3 = st.columns(3)
        col1.metric("Capital Banco", f"${sc(session, 'Capital Banco'):,.2f}")
        col2.metric("Reservas Legales", f"${sc(session, 'Reservas Legales'):,.2f}")
        col3.metric("Utilidades del Ejercicio", f"${sc(session, 'Utilidades del Ejercicio'):,.2f}")

        st.markdown("---")
        col4, col5 = st.columns(2)
        with col4:
            st.markdown("**Constituir Reserva Legal (BCR)**")
            res_monto = st.number_input("Monto de reserva ($)", min_value=10.0, value=500.0, step=100.0, key="res_monto")
            st.caption("Requiere saldo en Utilidades del Ejercicio")
            if st.button("🏦 Constituir Reserva Legal", key="btn_res"):
                try:
                    ok, msg = constituir_reserva_legal(session, res_monto)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("RESERVA_LEGAL", f"${res_monto}", "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))
        with col5:
            st.markdown("**Registrar Utilidades del Ejercicio**")
            util_monto = st.number_input("Monto de utilidades ($)", min_value=10.0, value=1000.0, step=100.0, key="util_monto")
            st.caption("Cierre contable: traslada resultado del período")
            if st.button("📈 Registrar Utilidades", key="btn_util"):
                try:
                    ok, msg = registrar_utilidad_ejercicio(session, util_monto)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("UTILIDADES_REGISTRAR", f"${util_monto}", "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

    # ── Tab 6: COMISIONES TC ────────────────────────────────────────
    with tabs[6]:
        st.subheader("Ingresos por Tarjeta de Crédito")
        st.info("Registra las comisiones que el banco cobra al comercio cuando clientes usan su tarjeta de crédito.")
        from contabilidad import saldo_cuenta as sc
        tc_monto = st.number_input("Monto comisión ($)", min_value=1.0, value=25.0, step=5.0, key="tc_monto")
        tc_desc  = st.text_input("Referencia / Comercio", value="Comisión TC — Comercio", key="tc_desc")
        if st.button("💳 Registrar Comisión TC", key="btn_tc"):
            try:
                ok, msg = registrar_comision_tarjeta_credito(session, tc_monto, tc_desc)
                session.commit()
                alert("success" if ok else "error", msg)
                _audit("COMISION_TC", tc_desc, "OK" if ok else "ERROR")
            except Exception as e:
                session.rollback(); alert("error", str(e))
        st.metric("Total Ingresos Tarjeta Crédito", f"${sc(session, 'Ingresos Tarjeta Credito'):,.2f}")

    # ── Tab 7: GASTOS OPERATIVOS ─────────────────────────────────────
    with tabs[7]:
        st.subheader("Gastos Operativos del Banco")
        from contabilidad import saldo_cuenta as sc
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Registrar gasto operativo**")
            go_monto = st.number_input("Monto ($)", min_value=10.0, value=500.0, step=100.0, key="go_monto")
            go_desc  = st.selectbox("Categoría", [
                "Salarios del mes", "Alquiler de oficinas", "Servicios públicos",
                "Mantenimiento de sistemas", "Papelería y útiles", "Publicidad",
                "Gastos de representación", "Otro"
            ], key="go_cat")
            go_det = st.text_input("Detalle adicional", key="go_det")
            if st.button("💼 Registrar Gasto Operativo", key="btn_go"):
                desc_completa = f"{go_desc}" + (f" — {go_det}" if go_det else "")
                try:
                    ok, msg = registrar_gasto_operativo(session, go_monto, desc_completa)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("GASTO_OPERATIVO", desc_completa, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))
        with col2:
            st.markdown("**Registrar mora pagada por el banco**")
            mp_monto = st.number_input("Monto multa/mora ($)", min_value=10.0, value=100.0, step=10.0, key="mp_monto")
            mp_desc  = st.text_input("Descripción", value="Multa regulatoria BCR", key="mp_desc")
            if st.button("⚡ Registrar Mora Pagada", key="btn_mp"):
                try:
                    ok, msg = registrar_mora_pagada_banco(session, mp_monto, mp_desc)
                    session.commit()
                    alert("success" if ok else "error", msg)
                    _audit("MORA_PAGADA_BANCO", mp_desc, "OK" if ok else "ERROR")
                except Exception as e:
                    session.rollback(); alert("error", str(e))

        st.markdown("---")
        col3, col4 = st.columns(2)
        col3.metric("Gastos Operativos Totales", f"${sc(session, 'Gastos Operativos'):,.2f}")
        col4.metric("Gastos por Mora Pagada", f"${sc(session, 'Gastos por Mora Pagada'):,.2f}")

    # ── Tab 8: SALDOS DEL PLAN ───────────────────────────────────────
    with tabs[8]:
        st.subheader("Saldos de Todas las Cuentas Contables")
        from models import CuentaContable
        cuentas = session.query(CuentaContable).order_by(
            CuentaContable.categoria, CuentaContable.nombre).all()
        cat_actual = None
        for c in cuentas:
            if c.categoria != cat_actual:
                cat_actual = c.categoria
                colores = {"ACTIVO": "🟦", "PASIVO": "🟥", "PATRIMONIO": "🟩",
                           "INGRESO": "🟨", "GASTO": "🟧"}
                st.markdown(f"**{colores.get(cat_actual,'⬜')} {cat_actual}**")
            saldo = c.saldo(session)
            signo = "+" if saldo >= 0 else ""
            st.write(f"  • {c.nombre}: **{signo}${saldo:,.2f}**")


#  VISTA: MONITOR AML
# ═══════════════════════════════════════════════════════════

def vista_aml(session, usuario):
    render_header("🚨", "Monitor AML", "Prevención de Lavado de Dinero")
    tab1, tab2 = st.tabs(["Alertas Pendientes", "Historial"])

    def _color_nivel(n):
        return "#e74c3c" if n == "CRITICA" else "#f39c12"

    with tab1:
        alertas = obtener_alertas_aml(session, estado="PENDIENTE")
        if alertas:
            st.warning(f"⚠️ {len(alertas)} alerta(s) pendientes de revisión")
            for a in alertas:
                c = session.query(Cliente).filter_by(id=a.cliente_id).first()
                color = _color_nivel(a.nivel)
                st.markdown(f"""
<div style="background:#1e1e2e;border-left:4px solid {color};padding:12px 16px;border-radius:6px;margin-bottom:10px">
  <div style="display:flex;justify-content:space-between">
    <span style="color:{color};font-weight:700">{a.tipo}</span>
    <span style="color:#8a8694;font-size:.8rem">{a.fecha.strftime("%Y-%m-%d %H:%M") if a.fecha else "—"}</span>
  </div>
  <div style="color:#e8e4d9;margin:4px 0">{a.descripcion}</div>
  <div style="color:#8a8694;font-size:.82rem">Cliente: {c.nombre if c else "—"}</div>
</div>""", unsafe_allow_html=True)

            if tiene_permiso(usuario, "gestionar_aml"):
                st.divider()
                a_sel = st.selectbox("Seleccionar alerta para revisar", alertas,
                                     format_func=lambda a: f"#{a.id} — {a.tipo} ({a.nivel})")
                notas = st.text_area("Notas de revisión")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Marcar Revisada"):
                        ok, _ = revisar_alerta_aml(session, a_sel.id, usuario.username, notas, cerrar=False)
                        if ok:
                            session.commit()
                            st.success("Alerta marcada como revisada")
                            st.rerun()
                with col2:
                    if st.button("🔒 Cerrar Alerta"):
                        ok, _ = revisar_alerta_aml(session, a_sel.id, usuario.username, notas, cerrar=True)
                        if ok:
                            session.commit()
                            st.success("Alerta cerrada")
                            st.rerun()
        else:
            st.success("✅ No hay alertas AML pendientes")

    with tab2:
        todas = obtener_alertas_aml(session, estado="TODAS")
        if todas:
            data = []
            for a in todas:
                c = session.query(Cliente).filter_by(id=a.cliente_id).first()
                data.append({"#": a.id, "Tipo": a.tipo, "Nivel": a.nivel,
                              "Cliente": c.nombre if c else "—",
                              "Monto": f"${float(a.monto):,.2f}" if a.monto else "—",
                              "Estado": a.estado,
                              "Fecha": a.fecha.strftime("%Y-%m-%d %H:%M") if a.fecha else "—",
                              "Revisado por": a.revisado_por or "—"})
            st.dataframe(pd.DataFrame(data), use_container_width=True)


# ═══════════════════════════════════════════════════════════
#  VISTA: DASHBOARD GERENCIAL
# ═══════════════════════════════════════════════════════════

def vista_dashboard_gerencial(session):
    from datetime import date
    from contabilidad import caja_real
    render_header("📊", "Dashboard Gerencial", "Indicadores clave del banco")

    total_clientes  = session.query(func.count(Cliente.id)).filter(Cliente.estado == "ACTIVO").scalar() or 0
    total_prestamos_cnt = session.query(func.count(Prestamo.id)).filter(Prestamo.estado == "ACTIVO").scalar() or 0
    cartera         = session.query(func.coalesce(func.sum(Prestamo.saldo_pendiente), 0)).filter(Prestamo.estado == "ACTIVO").scalar()
    mora_total      = session.query(func.coalesce(func.sum(Prestamo.mora_acumulada), 0)).filter(Prestamo.estado == "ACTIVO").scalar()
    total_depositos = session.query(func.coalesce(func.sum(Cliente.saldo), 0)).filter(Cliente.estado == "ACTIVO").scalar()
    caja = caja_real(session)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("👥 Clientes activos", total_clientes)
    col2.metric("🏷️ Préstamos activos", total_prestamos_cnt)
    col3.metric("💼 Cartera total", f"${float(cartera):,.2f}")
    col4.metric("⚠️ Mora acumulada", f"${float(mora_total):,.2f}")

    col5, col6 = st.columns(2)
    col5.metric("💰 Total depósitos", f"${float(total_depositos):,.2f}")
    col6.metric("🏦 Caja actual", f"${float(caja):,.2f}")

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        rows = (session.query(Prestamo.clasificacion, func.sum(Prestamo.saldo_pendiente))
                .filter(Prestamo.estado == "ACTIVO")
                .group_by(Prestamo.clasificacion).all())
        if rows:
            df_prest = pd.DataFrame(rows, columns=["Clasificación", "Cartera"])
            fig = px.pie(df_prest, names="Clasificación", values="Cartera",
                         title="Cartera por clasificación",
                         color_discrete_sequence=COLORES)
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#E8E4D9")
            st.plotly_chart(fig, use_container_width=True)

    with col_r:
        rows2 = (session.query(Cliente.tipo, func.sum(Cliente.saldo))
                 .filter(Cliente.estado == "ACTIVO")
                 .group_by(Cliente.tipo).all())
        if rows2:
            df_dep = pd.DataFrame(rows2, columns=["Tipo", "Saldo"])
            fig2 = px.bar(df_dep, x="Tipo", y="Saldo",
                          title="Depósitos por tipo de cuenta",
                          color_discrete_sequence=COLORES)
            fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#E8E4D9")
            st.plotly_chart(fig2, use_container_width=True)

    # Balance General
    st.subheader("📋 Balance General")
    bg = generar_balance_general(session)
    col_a, col_p, col_pat = st.columns(3)
    with col_a:
        st.markdown("**ACTIVOS**")
        for nombre, val in bg["activos"]:
            st.markdown(f"&nbsp;&nbsp;{nombre}: **${val:,.2f}**")
        st.markdown(f"**Total Activo: ${bg['total_activo']:,.2f}**")
    with col_p:
        st.markdown("**PASIVOS**")
        for nombre, val in bg["pasivos"]:
            st.markdown(f"&nbsp;&nbsp;{nombre}: **${val:,.2f}**")
        st.markdown(f"**Total Pasivo: ${bg['total_pasivo']:,.2f}**")
    with col_pat:
        st.markdown("**PATRIMONIO**")
        for nombre, val in bg["patrimonio"]:
            st.markdown(f"&nbsp;&nbsp;{nombre}: **${val:,.2f}**")
        st.markdown(f"**Total Patrimonio: ${bg['total_patrimonio']:,.2f}**")

    eq = "✅ Ecuación cuadra" if bg["ecuacion_ok"] else "❌ Ecuación no cuadra"
    st.info(f"{eq} — A={bg['total_activo']:,.2f} = P+Pat={bg['total_pasivo']+bg['total_patrimonio']:,.2f}")

    # Estado de Resultados
    st.subheader("📊 Estado de Resultados")
    er = generar_estado_resultados(session)
    col_i, col_g = st.columns(2)
    with col_i:
        st.markdown("**INGRESOS**")
        for nombre, val in er["ingresos"]:
            st.markdown(f"&nbsp;&nbsp;{nombre}: **${val:,.2f}**")
        st.markdown(f"**Total Ingresos: ${er['total_ingresos']:,.2f}**")
    with col_g:
        st.markdown("**GASTOS**")
        for nombre, val in er["gastos"]:
            st.markdown(f"&nbsp;&nbsp;{nombre}: **${val:,.2f}**")
        st.markdown(f"**Total Gastos: ${er['total_gastos']:,.2f}**")

    utilidad = er["utilidad"]
    st.metric("💹 Utilidad Neta", f"${utilidad:,.2f}")

    # Cierre diario
    st.divider()
    st.subheader("🔒 Cierre Diario")
    hoy = date.today()
    ultimo_cierre = (session.query(CierreDiario).order_by(CierreDiario.fecha.desc()).first())
    if ultimo_cierre:
        st.info(f"Último cierre: {ultimo_cierre.fecha} | Caja final: ${float(ultimo_cierre.caja_final):,.2f}")
    else:
        st.warning("No se ha realizado ningún cierre diario.")

    cierre_hoy = session.query(CierreDiario).filter_by(fecha=hoy).first()
    if not cierre_hoy:
        notas_c = st.text_input("Notas del cierre", key="notas_cierre")
        if st.button("🔒 Realizar Cierre del Día", type="primary"):
            ok, result = realizar_cierre_diario(session, "Gerente", notas_c)
            if ok:
                session.commit()
                st.success(f"✅ Cierre realizado — Depósitos: ${float(result.total_depositos):,.2f} | Retiros: ${float(result.total_retiros):,.2f}")
                st.rerun()
            else:
                st.error(result)
    else:
        st.success(f"✅ Cierre de hoy ya realizado — Caja: ${float(cierre_hoy.caja_final):,.2f}")



def main():
    st.set_page_config(
        page_title="Sistema Bancario",
        page_icon="🏦",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_styles()
    session = get_session()

    try:
        inicializar_contabilidad(session)
        inicializar_tipos_cuenta(session)
        inicializar_sucursales(session)
        session.commit()

        # ── Primer arranque: sin usuarios ──
        if not hay_usuarios(session):
            pantalla_primer_admin(session)
            return

        # ── No autenticado: mostrar login ──
        if "usuario_id" not in st.session_state:
            pantalla_login(session)
            return

        # ── Timeout de sesión (30 minutos de inactividad) ──
        SESSION_TIMEOUT_MIN = 30
        inicio_str = st.session_state.get("session_inicio")
        if inicio_str:
            desde = datetime.utcnow() - datetime.fromisoformat(inicio_str)
            if desde.total_seconds() > SESSION_TIMEOUT_MIN * 60:
                _claves = ["usuario_id","usuario_nombre","usuario_rol",
                           "usuario_username","session_inicio"]
                for k in _claves:
                    st.session_state.pop(k, None)
                st.warning("⏱ Sesión expirada por inactividad. Ingresa nuevamente.")
                st.rerun()

        # ── Cargar usuario activo ──
        usuario = session.query(Usuario).filter_by(
            id=st.session_state["usuario_id"], activo=True
        ).first()
        if not usuario:
            # usuario desactivado mientras estaba logueado
            for k in ["usuario_id","usuario_nombre","usuario_rol","usuario_username"]:
                st.session_state.pop(k, None)
            st.rerun()

        # ── Sidebar ──
        with st.sidebar:
            color_rol = ROL_COLOR.get(usuario.rol, "#8A8694")
            ROL_ICONS = {"ADMIN": "🔑", "GERENTE": "📊", "CAJERO": "💵", "AUDITOR": "🔍"}
            st.markdown(f"""
<div style="display:flex;align-items:center;gap:12px;padding:10px 0 22px">
  <div style="width:40px;height:40px;background:#5C5464;border-radius:12px;
              display:flex;align-items:center;justify-content:center;font-size:20px">🏦</div>
  <div>
    <div style="color:#E8E4D9;font-weight:700;font-size:1rem;">BancoApp</div>
    <div style="color:#6B6478;font-size:.72rem;">Sistema Bancario</div>
  </div>
</div>
<hr style="border-color:#3D3B3C;margin-bottom:14px">
<div style="background:#2A2535;border-radius:10px;padding:10px 12px;margin-bottom:16px">
  <div style="font-size:.75rem;color:#8A8694;margin-bottom:2px">Sesión activa</div>
  <div style="font-weight:600;color:#E8E4D9;font-size:.9rem">{usuario.nombre}</div>
  <div style="font-size:.75rem;color:{color_rol}">{ROL_ICONS.get(usuario.rol,'')}&nbsp;{usuario.rol}</div>
</div>
""", unsafe_allow_html=True)

            menu = MENU_POR_ROL.get(usuario.rol, ["📊 Panel principal"])
            opcion = st.radio("Navegación", menu, label_visibility="collapsed")
            # renovar timestamp de actividad en cada navegación
            st.session_state["session_inicio"] = datetime.utcnow().isoformat()

            st.divider()
            _render_alertas_badge(session)
            if st.button("🚪 Cerrar sesión", use_container_width=True):
                registrar_log(session, usuario, "LOGOUT", "Cerró sesión", "OK")
                session.commit()
                for k in ["usuario_id", "usuario_nombre", "usuario_rol", "usuario_username"]:
                    st.session_state.pop(k, None)
                st.rerun()

            if usuario.rol == "ADMIN":
                with st.expander("🔧 Modo desarrollo"):
                    st.caption("Crea clientes temporales, ejecuta 500 operaciones aleatorias, verifica integridad y limpia automáticamente. No requiere preparación previa.")
                    if st.button("🔥 Stress Test", type="secondary"):
                        dev_session = get_session()
                        try:
                            ejecutar_stress_test(dev_session)
                        finally:
                            dev_session.close()

        # ── Despacho de vistas con guardias de permiso ──
        if opcion == "📊 Panel principal":
            vista_panel(session)
        elif opcion == "👥 Clientes":
            if tiene_permiso(usuario, "ver_clientes"):
                vista_clientes(session, usuario)
            else:
                _acceso_denegado("ver_clientes")
        elif opcion == "💵 Operaciones bancarias":
            if tiene_permiso(usuario, "operaciones_bancarias"):
                vista_operaciones(session)
            else:
                _acceso_denegado("operaciones_bancarias")
        elif opcion == "🏷️ Préstamos":
            if tiene_permiso(usuario, "ver_prestamos"):
                vista_prestamos(session)
            else:
                _acceso_denegado("ver_prestamos")
        elif opcion == "📈 Reportes contables":
            if tiene_permiso(usuario, "ver_reportes"):
                vista_reportes(session)
            else:
                _acceso_denegado("ver_reportes")
        elif opcion == "🔍 Reconciliación":
            if tiene_permiso(usuario, "ver_reconciliacion"):
                vista_reconciliacion(session)
            else:
                _acceso_denegado("ver_reconciliacion")
        elif opcion == "👤 Gestión de usuarios":
            if tiene_permiso(usuario, "gestionar_usuarios"):
                vista_usuarios(session, usuario)
            else:
                _acceso_denegado("gestionar_usuarios")
        elif opcion == "📋 Log de auditoría":
            if tiene_permiso(usuario, "ver_auditlog"):
                vista_auditoria(session)
            else:
                _acceso_denegado("ver_auditlog")
        elif opcion == "⚙️ Configuración":
            if tiene_permiso(usuario, "configurar_banco"):
                vista_configuracion(session, usuario)
            else:
                _acceso_denegado("configurar_banco")
        elif opcion == "🔔 Alertas":
            if tiene_permiso(usuario, "ver_alertas"):
                vista_alertas(session)
            else:
                _acceso_denegado("ver_alertas")
        elif opcion == "💳 Tarjetas":
            if tiene_permiso(usuario, "ver_tarjetas"):
                vista_tarjetas(session, usuario)
            else:
                _acceso_denegado("ver_tarjetas")
        elif opcion == "🏦 Sucursales & ATM":
            if tiene_permiso(usuario, "ver_sucursales"):
                vista_sucursales(session, usuario)
            else:
                _acceso_denegado("ver_sucursales")
        elif opcion == "📑 Plazo Fijo":
            if tiene_permiso(usuario, "ver_plazo_fijo"):
                vista_plazo_fijo(session, usuario)
            else:
                _acceso_denegado("ver_plazo_fijo")
        elif opcion == "🤝 Socios":
            if tiene_permiso(usuario, "ver_socios"):
                vista_socios(session, usuario)
            else:
                _acceso_denegado("ver_socios")
        elif opcion == "🚨 Monitor AML":
            if tiene_permiso(usuario, "ver_aml"):
                vista_aml(session, usuario)
            else:
                _acceso_denegado("ver_aml")
        elif opcion == "🏦 Contabilidad Avanzada":
            if tiene_permiso(usuario, "reportes"):
                vista_contabilidad_avanzada(session, usuario)
            else:
                _acceso_denegado("reportes")
        elif opcion == "📊 Dashboard Gerencial":
            if tiene_permiso(usuario, "ver_dashboard_gerencial"):
                vista_dashboard_gerencial(session)
            else:
                _acceso_denegado("ver_dashboard_gerencial")

    finally:
        session.close()


if __name__ == "__main__":
    main()