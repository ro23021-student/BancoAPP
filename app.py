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
from sqlalchemy.orm import Session

from models import (
    BaseLocal, Cliente, Movimiento, Prestamo,
    CuentaContable, Asiento, LineaAsiento, ConfigBanco,
)
from contabilidad import (
    inicializar_contabilidad, saldo_cuenta, caja_real,
    reconciliar, PLAN_CUENTAS,
)
from operaciones import (
    crear_cliente, depositar, retirar, transferir,
    otorgar_prestamo, pagar_prestamo, devengar_interes,
)

# ─────────────────────────────
# ENGINE  (una sola instancia)
# ─────────────────────────────
@st.cache_resource
def get_engine():
    engine = create_engine(
        "sqlite:///banco.db",
        connect_args={"check_same_thread": False},
    )
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

    st.markdown("#### Últimos 10 movimientos")
    ultimos = (
        session.query(Movimiento)
        .order_by(Movimiento.fecha.desc())
        .limit(10).all()
    )
    render_movimientos(ultimos)


def vista_clientes(session):
    render_header("👥", "Gestión de clientes")
    tab1, tab2, tab3 = st.tabs(["📋 Lista de clientes", "➕ Nuevo cliente", "📜 Historial"])

    with tab1:
        clientes = session.query(Cliente).order_by(Cliente.nombre).all()
        if clientes:
            df = pd.DataFrame([{
                "ID":      c.id,
                "Nombre":  c.nombre,
                "Tipo":    c.tipo.capitalize(),
                "Saldo":   f"${c.saldo:,.2f}",
                "Creado":  c.creado_en.strftime("%Y-%m-%d") if c.creado_en else "—",
            } for c in clientes])
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"Total de clientes: {len(clientes)}")
        else:
            alert("info", "No hay clientes registrados aún.")

    with tab2:
        # BUG CORREGIDO: el bloque if/else estaba fuera del form, causando que
        # 'ok' y 'msg' no estuvieran definidas si nombre estaba vacío.
        with st.form("nuevo_cliente", clear_on_submit=True):
            nombre        = st.text_input("Nombre completo *")
            tipo          = st.selectbox("Tipo de cuenta", ["ahorro", "corriente"])
            saldo_inicial = st.number_input(
                "Saldo inicial ($)",
                min_value=0.0, step=100.0, max_value=500_000.0,
                help="El banco no cobra comisión al abrir la cuenta.",
            )
            enviado = st.form_submit_button("✅ Crear cliente", type="primary")
        if enviado:
            if not nombre.strip():
                alert("error", "✗ El nombre no puede estar vacío.")
            else:
                ok, msg = crear_cliente(session, nombre, tipo, saldo_inicial)
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                if ok:
                    st.rerun()

    with tab3:
        clientes = session.query(Cliente).order_by(Cliente.nombre).all()
        if clientes:
            sel = st.selectbox(
                "Seleccionar cliente",
                clientes,
                format_func=lambda c: f"[{c.id}] {c.nombre}  —  ${c.saldo:,.2f}",
                key="hist_sel",
            )
            movs = (
                session.query(Movimiento)
                .filter_by(cliente_id=sel.id)
                .order_by(Movimiento.fecha.desc())
                .all()
            )
            col_a, col_b = st.columns([2, 1])
            with col_a:
                st.markdown(f"**Movimientos de {sel.nombre}**")
            with col_b:
                color = "#10B981" if sel.saldo >= 0 else "#F87171"
                st.markdown(
                    f"<div style='text-align:right;font-family:DM Mono,monospace;"
                    f"font-size:1.1rem;font-weight:600;color:{color}'>"
                    f"${sel.saldo:,.2f}</div>",
                    unsafe_allow_html=True,
                )
            render_movimientos(movs)
        else:
            alert("info", "No hay clientes registrados.")


def vista_operaciones(session):
    render_header("💵", "Operaciones bancarias")
    operacion = st.radio(
        "Operación",
        ["💰 Depósito", "🏧 Retiro", "↔️ Transferencia"],
        horizontal=True,
    )
    clientes = session.query(Cliente).order_by(Cliente.nombre).all()

    if not clientes:
        alert("info", "No hay clientes. Cree un cliente primero.")
        return

    st.divider()

    # ── Depósito ──
    if "Depósito" in operacion:
        st.markdown("**Depósito en ventanilla** — se aplica comisión del 2% sobre el monto bruto.")
        with st.form("deposito_form"):
            sel   = st.selectbox("Cliente", clientes,
                                 format_func=lambda c: f"[{c.id}] {c.nombre}  —  ${c.saldo:,.2f}")
            monto = st.number_input("Monto bruto ($)", min_value=0.01, step=100.0,
                                    max_value=500_000.0)
            st.info(
                f"💡  Neto al cliente: **${float(monto)*0.98:,.2f}**  ·  "
                f"Comisión banco: **${float(monto)*0.02:,.2f}**"
            )
            enviado = st.form_submit_button("💰 Realizar depósito", type="primary")
        if enviado:
            ok, msg = depositar(session, sel.id, monto)
            alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
            if ok: st.rerun()

    # ── Retiro ──
    elif "Retiro" in operacion:
        sel = st.selectbox("Cliente", clientes,
                           format_func=lambda c: f"[{c.id}] {c.nombre}  —  ${c.saldo:,.2f}",
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
        # BUG CORREGIDO: chequeo duplicado de saldo <= 0 eliminado
        if sel.saldo <= 0:
            alert("warning", "El cliente no tiene saldo disponible.")
        else:
            with st.form("retiro_form"):
                monto   = st.number_input("Monto a retirar ($)",
                                          min_value=0.01, max_value=float(sel.saldo), step=100.0)
                enviado = st.form_submit_button("🏧 Realizar retiro", type="primary")
            if enviado:
                ok, msg = retirar(session, sel.id, monto)
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                if ok: st.rerun()

    # ── Transferencia ──
    else:
        st.markdown("**Transferencia entre cuentas** — comisión del 1% a cargo del origen.")
        with st.form("transferencia_form"):
            col1, col2 = st.columns(2)
            with col1:
                origen  = st.selectbox("Cuenta origen",  clientes,
                                       format_func=lambda c: f"[{c.id}] {c.nombre}  —  ${c.saldo:,.2f}")
            with col2:
                destino = st.selectbox("Cuenta destino", clientes,
                                       format_func=lambda c: f"[{c.id}] {c.nombre}  —  ${c.saldo:,.2f}")
            monto = st.number_input("Monto a transferir ($)", min_value=0.01,
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
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                if ok: st.rerun()


def vista_prestamos(session):
    render_header("🏷️", "Gestión de préstamos")
    tab1, tab2, tab3 = st.tabs([
        "➕ Otorgar préstamo",
        "💳 Pagar préstamo",
        "📋 Préstamos activos",
    ])
    clientes = session.query(Cliente).order_by(Cliente.nombre).all()

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
            alert("info",
                  f"Capacidad de crédito disponible: <strong>${capacidad:,.2f}</strong>"
                  f"   ·   (Capital: ${capital_banco:,.2f} – Préstamos activos: ${total_prest:,.2f})"
                  f"   ·   Interés fijo: <strong>10%</strong>")
            with st.form("prestamo_form"):
                sel   = st.selectbox("Cliente", clientes,
                                     format_func=lambda c: f"[{c.id}] {c.nombre}")
                monto = st.number_input("Monto del préstamo ($)", min_value=100.0,
                                        step=500.0, max_value=500_000.0)
                st.info(
                    f"💡  Capital: **${float(monto):,.2f}**  ·  "
                    f"Interés (10%): **${float(monto)*0.10:,.2f}**  ·  "
                    f"Deuda total: **${float(monto)*1.10:,.2f}**"
                )
                enviado = st.form_submit_button("✅ Otorgar préstamo", type="primary")
            if enviado:
                ok, msg = otorgar_prestamo(session, sel.id, monto)
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
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
                    max_pago = float(min(sel_c.saldo, Decimal(str(deuda_tot))))
                    monto_p = st.number_input(
                        "Monto a pagar ($)",
                        min_value=0.01,
                        max_value=max_pago if max_pago > 0 else 0.01,
                        step=100.0,
                    )
                    p_int = min(monto_p, int_pend)
                    p_cap = monto_p - p_int
                    st.caption(
                        f"Se aplicará → capital: **${p_cap:.2f}** · interés: **${p_int:.2f}**"
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
            } for p in activos])
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("**📅 Devengo de intereses mensual**")
            st.caption(
                "Registra 1/12 del interés anual de cada préstamo como "
                "Intereses x Cobrar (activo) e Ingresos Intereses."
            )
            if st.button("📅 Devengar intereses del período", type="primary"):
                ok, msg = devengar_interes(session)
                alert("success" if ok else "error", ("✓ " if ok else "✗ ") + msg)
                if ok: st.rerun()
        else:
            alert("info", "No hay préstamos activos con saldo pendiente.")


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
        caja      = saldo_cuenta(session, "Caja General")
        prest_c   = saldo_cuenta(session, "Prestamos x Cobrar")
        int_c     = saldo_cuenta(session, "Intereses x Cobrar")
        depositos = saldo_cuenta(session, "Depositos Clientes")
        capital   = saldo_cuenta(session, "Capital Banco")
        ing_int   = saldo_cuenta(session, "Ingresos Intereses")
        ing_com   = saldo_cuenta(session, "Ingresos Comisiones")

        activos    = caja + prest_c + int_c
        pasivos    = depositos
        patrimonio = capital + ing_int + ing_com
        diff       = round(activos - pasivos - patrimonio, 2)

        st.markdown("**ACTIVOS**")
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a: render_metric("Caja General",        f"${caja:,.2f}")
        with col_b: render_metric("Préstamos x Cobrar",  f"${prest_c:,.2f}")
        with col_c: render_metric("Intereses x Cobrar",  f"${int_c:,.2f}")
        with col_d: render_metric("TOTAL ACTIVOS",       f"${activos:,.2f}")

        st.markdown("**PASIVOS + PATRIMONIO**")
        col_e, col_f, col_g, col_h, col_i = st.columns(5)
        with col_e: render_metric("Depósitos Clientes",  f"${depositos:,.2f}")
        with col_f: render_metric("Capital Banco",        f"${capital:,.2f}")
        with col_g: render_metric("Ingresos Intereses",       f"${ing_int:,.2f}")
        with col_h: render_metric("Ingresos Comisiones",      f"${ing_com:,.2f}")
        with col_i: render_metric("TOTAL PAS+PAT",        f"${pasivos+patrimonio:,.2f}")

        st.markdown("")
        if abs(diff) < 0.01:
            alert("success", f"✅ Balance cuadrado — diferencia: ${diff:.2f}")
        else:
            alert("error", f"❌ Balance descuadrado — diferencia: ${diff:,.2f}")

        composicion = {"Caja": caja, "Préstamos": prest_c, "Intereses x Cobrar": int_c}
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


# ─────────────────────────────
# STRESS TEST (modo desarrollo)
# ─────────────────────────────
def ejecutar_stress_test(session):
    clientes = session.query(Cliente).all()
    if len(clientes) < 2:
        st.error("Necesitas al menos 2 clientes para el stress test.")
        return

    operaciones_realizadas = 0
    errores_controlados    = 0

    for _ in range(5000):  # reducido de 10000 a 500 para no congelar la UI
        try:
            operacion = random.choice([
                "deposito", "retiro", "transferencia", "prestamo", "pago_prestamo",
            ])
            cliente = random.choice(clientes)
            # Refrescar el cliente desde la BD para tener saldo actualizado
            session.refresh(cliente)
            monto = Decimal(str(random.randint(10, 500)))

            if operacion == "deposito":
                ok, _ = depositar(session, cliente.id, monto)
            elif operacion == "retiro":
                ok, _ = retirar(session, cliente.id, monto)
            elif operacion == "prestamo":
                ok, _ = otorgar_prestamo(session, cliente.id, monto)
            elif operacion == "pago_prestamo":
                # BUG CORREGIDO: se pasa cliente_id, no prestamo.id
                ok, _ = pagar_prestamo(session, cliente.id, monto)
            elif operacion == "transferencia":
                otro = random.choice(clientes)
                if cliente.id == otro.id:
                    continue
                ok, _ = transferir(session, cliente.id, otro.id, monto)
            else:
                continue

            if ok:
                operaciones_realizadas += 1
            else:
                errores_controlados += 1

        except Exception as e:
            errores_controlados += 1
            session.rollback()

    st.success(f"Stress test terminado — {operaciones_realizadas} operaciones exitosas, "
               f"{errores_controlados} rechazos controlados.")

    # Verificar saldos negativos
    clientes_actualizados = session.query(Cliente).all()
    negativos = [c for c in clientes_actualizados if c.saldo < 0]
    if negativos:
        for c in negativos:
            st.error(f"🚨 SALDO NEGATIVO detectado: {c.nombre} = ${c.saldo:,.2f}")
    else:
        st.success("✅ Ningún cliente con saldo negativo.")

    st.subheader("Reconciliación post stress test")
    errores, lines = reconciliar(session)
    for line in lines:
        if "✅" in line:
            st.success(line.strip())
        elif "❌" in line:
            st.error(line.strip())
        elif line.strip():
            st.code(line, language=None)


# ─────────────────────────────
# MAIN
# ─────────────────────────────
def main():
    st.set_page_config(
        page_title="Sistema Bancario",
        page_icon="🏦",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_styles()

    with st.sidebar:
        st.markdown("""
<div style="display:flex;align-items:center;gap:12px;padding:10px 0 22px">
  <div style="width:40px;height:40px;background:#5C5464;border-radius:12px;
              display:flex;align-items:center;justify-content:center;font-size:20px">🏦</div>
  <div>
    <div style="color:#E8E4D9;font-weight:700;font-size:1rem;">BancoApp</div>
    <div style="color:#6B6478;font-size:.72rem;">Sistema Bancario</div>
  </div>
</div>
<hr style="border-color:#3D3B3C;margin-bottom:14px">
        """, unsafe_allow_html=True)

        opcion = st.radio("Navegación", [
            "📊 Panel principal",
            "👥 Clientes",
            "💵 Operaciones bancarias",
            "🏷️ Préstamos",
            "📈 Reportes contables",
            "🔍 Reconciliación",
        ], label_visibility="collapsed")

        # BUG CORREGIDO: stress test movido al sidebar donde no interfiere con el flujo principal
        st.divider()
        with st.expander("🔧 Modo desarrollo"):
            st.caption("Ejecuta 500 operaciones aleatorias y verifica la integridad.")
            if st.button("🔥 Stress Test", type="secondary"):
                dev_session = get_session()
                try:
                    ejecutar_stress_test(dev_session)
                finally:
                    dev_session.close()

    session = get_session()
    try:
        inicializar_contabilidad(session)

        if   opcion == "📊 Panel principal":       vista_panel(session)
        elif opcion == "👥 Clientes":              vista_clientes(session)
        elif opcion == "💵 Operaciones bancarias": vista_operaciones(session)
        elif opcion == "🏷️ Préstamos":             vista_prestamos(session)
        elif opcion == "📈 Reportes contables":    vista_reportes(session)
        elif opcion == "🔍 Reconciliación":        vista_reconciliacion(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()