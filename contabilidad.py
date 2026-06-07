"""
contabilidad.py — Motor de contabilidad de partida doble
Sin dependencias externas: solo SQLAlchemy.

PLAN DE CUENTAS COMPLETO:
  ACTIVOS      (tipo_normal=D): Caja General, Prestamos x Cobrar, Intereses x Cobrar,
                                 Inversiones, Bienes e Inmuebles, Deudores por Tarjeta,
                                 Prestamos Morosos
                                 Provision Incobrables (contra-activo, tipo_normal=C)
  PASIVOS      (tipo_normal=C): Cuentas de Ahorro, Cuentas Corrientes,
                                 Depositos Clientes (retrocompat), Depositos a Plazo Fijo,
                                 Obligaciones con Bancos, Impuestos por Pagar
  PATRIMONIO   (tipo_normal=C): Capital Banco, Reservas Legales, Utilidades del Ejercicio
  INGRESOS     (tipo_normal=C): Ingresos Intereses, Ingresos Comisiones,
                                 Ingresos Tarjeta Credito, Ingresos por Mora
  GASTOS       (tipo_normal=D): Gastos Intereses, Gastos Operativos,
                                 Gastos por Provisiones, Gastos por Mora Pagada
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from models import CuentaContable, Asiento, LineaAsiento, ConfigBanco


# ─── Helpers decimales ───────────────────────────────────────

def money(v):
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def f(v):
    return money(v)


# ─── Plan de cuentas ─────────────────────────────────────────

PLAN_CUENTAS = [
    # ── ACTIVOS ─────────────────────────────────────────────
    ("Caja General",               "ACTIVO",     "D"),
    ("Prestamos x Cobrar",         "ACTIVO",     "D"),
    ("Intereses x Cobrar",         "ACTIVO",     "D"),
    ("Deudores por Tarjeta",       "ACTIVO",     "D"),
    # Nuevas cuentas de activo
    ("Inversiones",                "ACTIVO",     "D"),  # valores, bonos, inversiones
    ("Bienes e Inmuebles",         "ACTIVO",     "D"),  # edificios, equipos, mobiliario
    ("Prestamos Morosos",          "ACTIVO",     "D"),  # préstamos en mora separados
    ("Provision Incobrables",      "ACTIVO",     "C"),  # contra-activo (reduce activos)

    # ── PASIVOS ─────────────────────────────────────────────
    ("Depositos Clientes",         "PASIVO",     "C"),  # retrocompatibilidad
    ("Depositos a Plazo Fijo",     "PASIVO",     "C"),
    # Nuevas cuentas de pasivo
    ("Cuentas de Ahorro",          "PASIVO",     "C"),  # depósitos de ahorro
    ("Cuentas Corrientes",         "PASIVO",     "C"),  # cuentas corrientes
    ("Obligaciones con Bancos",    "PASIVO",     "C"),  # préstamos de otros bancos
    ("Impuestos por Pagar",        "PASIVO",     "C"),  # IVA, renta

    # ── PATRIMONIO ──────────────────────────────────────────
    ("Capital Banco",              "PATRIMONIO", "C"),
    # Nuevas cuentas de patrimonio
    ("Reservas Legales",           "PATRIMONIO", "C"),  # reserva legal BCR El Salvador
    ("Utilidades del Ejercicio",   "PATRIMONIO", "C"),  # ganancias del período

    # ── INGRESOS ────────────────────────────────────────────
    ("Ingresos Intereses",         "INGRESO",    "C"),
    ("Ingresos Comisiones",        "INGRESO",    "C"),
    # Nuevos ingresos
    ("Ingresos Tarjeta Credito",   "INGRESO",    "C"),  # comisiones al comercio por TC
    ("Ingresos por Mora",          "INGRESO",    "C"),  # intereses de penalización

    # ── GASTOS ──────────────────────────────────────────────
    ("Gastos Intereses",           "GASTO",      "D"),
    # Nuevos gastos
    ("Gastos Operativos",          "GASTO",      "D"),  # salarios, alquiler, servicios
    ("Gastos por Provisiones",     "GASTO",      "D"),  # cuando un préstamo se vuelve incobrable
    ("Gastos por Mora Pagada",     "GASTO",      "D"),  # multas que paga el banco
]

CAPITAL_INICIAL = Decimal("10000.0")


# ─── Inicialización ──────────────────────────────────────────

def inicializar_contabilidad(session):
    """Crea el plan de cuentas y el capital inicial (solo una vez)."""
    # Crear cuentas faltantes
    for nombre, cat, tn in PLAN_CUENTAS:
        if not session.query(CuentaContable).filter_by(nombre=nombre).first():
            session.add(CuentaContable(nombre=nombre, categoria=cat, tipo_normal=tn))
    session.flush()

    # Capital inicial — solo una vez
    cfg = session.query(ConfigBanco).filter_by(clave="capital_inicial").first()
    if not cfg:
        registrar(session,
                  debitos=[("Caja General", CAPITAL_INICIAL)],
                  creditos=[("Capital Banco", CAPITAL_INICIAL)],
                  descripcion="Aporte capital inicial")
        session.add(ConfigBanco(clave="capital_inicial", valor=str(CAPITAL_INICIAL)))
    session.commit()


# ─── Núcleo contable ─────────────────────────────────────────

def get_cuenta(session, nombre) -> CuentaContable:
    c = session.query(CuentaContable).filter_by(nombre=nombre).first()
    if not c:
        raise ValueError(f"Cuenta contable no encontrada: '{nombre}'")
    return c


def registrar(session, debitos: list, creditos: list, descripcion: str):
    """
    Registra un asiento de partida doble.
    for _, monto in debitos + creditos:
        monto = money(monto)

        if monto <= 0:
            raise ValueError("Monto inválido en asiento")
    debitos  = [(nombre_cuenta, monto), ...]
    creditos = [(nombre_cuenta, monto), ...]
    Valida que total débitos == total créditos.
    """
    total_d = sum(money(m) for _, m in debitos)
    total_c = sum(money(m) for _, m in creditos)
    if abs(total_d - total_c) > 0.005:
        raise ValueError(
            f"Asiento descuadrado: débitos={total_d:.2f} ≠ créditos={total_c:.2f} | {descripcion}"
        )

    asiento = Asiento(descripcion=descripcion)
    session.add(asiento)
    session.flush()

    for nombre, monto in debitos:
        cuenta = get_cuenta(session, nombre)
        session.add(LineaAsiento(
            asiento_id=asiento.id,
            cuenta_id=cuenta.id,
            debito=f(monto),
            credito=Decimal("0.00"),
        ))

    for nombre, monto in creditos:
        cuenta = get_cuenta(session, nombre)
        session.add(LineaAsiento(
            asiento_id=asiento.id,
            cuenta_id=cuenta.id,
            debito=Decimal("0.00"),
            credito=f(monto),
        ))

    session.flush()
    return asiento


def saldo_cuenta(session, nombre: str) -> float:
    cuenta = session.query(CuentaContable).filter_by(nombre=nombre).first()
    if not cuenta:
        return 0.0
    return cuenta.saldo(session)


def caja_real(session) -> float:
    return saldo_cuenta(session, "Caja General")


# ─── Reconciliación ──────────────────────────────────────────

def reconciliar(session):
    """
    Verifica tres invariantes:
      1. Ecuación contable: Activos = Pasivos + Patrimonio + Ingresos Netos
      2. Depósitos contables ≈ suma de saldos de clientes
      3. Total débitos == Total créditos en el libro mayor
    """
    from models import Cliente, Prestamo
    from sqlalchemy import func

    # ── ACTIVOS ──────────────────────────────────────────────
    caja          = saldo_cuenta(session, "Caja General")
    prest_c       = saldo_cuenta(session, "Prestamos x Cobrar")
    int_c         = saldo_cuenta(session, "Intereses x Cobrar")
    tarjetas_c    = saldo_cuenta(session, "Deudores por Tarjeta")
    inversiones   = saldo_cuenta(session, "Inversiones")
    inmuebles     = saldo_cuenta(session, "Bienes e Inmuebles")
    morosos       = saldo_cuenta(session, "Prestamos Morosos")
    provision     = saldo_cuenta(session, "Provision Incobrables")   # contra-activo (ya negativo)

    # ── PASIVOS ──────────────────────────────────────────────
    dep_clientes  = saldo_cuenta(session, "Depositos Clientes")
    plazos_fijos  = saldo_cuenta(session, "Depositos a Plazo Fijo")
    ahorros       = saldo_cuenta(session, "Cuentas de Ahorro")
    corrientes    = saldo_cuenta(session, "Cuentas Corrientes")
    oblig_bancos  = saldo_cuenta(session, "Obligaciones con Bancos")
    impuestos_pp  = saldo_cuenta(session, "Impuestos por Pagar")

    # ── PATRIMONIO ───────────────────────────────────────────
    capital       = saldo_cuenta(session, "Capital Banco")
    reservas      = saldo_cuenta(session, "Reservas Legales")
    utilidades    = saldo_cuenta(session, "Utilidades del Ejercicio")

    # ── INGRESOS ─────────────────────────────────────────────
    ing_int       = saldo_cuenta(session, "Ingresos Intereses")
    ing_com       = saldo_cuenta(session, "Ingresos Comisiones")
    ing_tc        = saldo_cuenta(session, "Ingresos Tarjeta Credito")
    ing_mora      = saldo_cuenta(session, "Ingresos por Mora")

    # ── GASTOS ───────────────────────────────────────────────
    gast_int      = saldo_cuenta(session, "Gastos Intereses")
    gast_oper     = saldo_cuenta(session, "Gastos Operativos")
    gast_prov     = saldo_cuenta(session, "Gastos por Provisiones")
    gast_mora     = saldo_cuenta(session, "Gastos por Mora Pagada")

    # Provision Incobrables es contra-activo: su saldo reduce el total de activos
    activos    = round(caja + prest_c + int_c + tarjetas_c + inversiones + inmuebles + morosos - provision, 2)
    pasivos    = round(dep_clientes + plazos_fijos + ahorros + corrientes + oblig_bancos + impuestos_pp, 2)
    ing_total  = ing_int + ing_com + ing_tc + ing_mora
    gast_total = gast_int + gast_oper + gast_prov + gast_mora
    patrimonio = round(capital + reservas + utilidades + ing_total - gast_total, 2)
    diff_bal   = round(activos - pasivos - patrimonio, 2)

    # Débitos vs créditos globales
    total_deb = session.query(func.coalesce(func.sum(LineaAsiento.debito),  0.0)).scalar()
    total_cre = session.query(func.coalesce(func.sum(LineaAsiento.credito), 0.0)).scalar()
    diff_libro = round(float(total_deb) - float(total_cre), 2)

    suma_clientes = float(
        session.query(func.coalesce(func.sum(Cliente.saldo), 0.0)).scalar()
    )

    dep_total = dep_clientes + ahorros + corrientes

    diff_dep = round(suma_clientes - dep_total, 2)

    errores = 0
    lines   = []

    lines += [
        "═══════════════ BALANCE GENERAL ═══════════════",
        "── ACTIVOS ─────────────────────────────────────",
        f"  Caja General:              ${caja:>12,.2f}",
        f"  Prestamos x Cobrar:        ${prest_c:>12,.2f}",
        f"  Intereses x Cobrar:        ${int_c:>12,.2f}",
        f"  Deudores por Tarjeta:      ${tarjetas_c:>12,.2f}",
        f"  Inversiones:               ${inversiones:>12,.2f}",
        f"  Bienes e Inmuebles:        ${inmuebles:>12,.2f}",
        f"  Préstamos Morosos:         ${morosos:>12,.2f}",
        f"  (-) Provisión Incobrables: ${-provision:>12,.2f}",
        f"  ─────────────────────────────────────────────",
        f"  TOTAL ACTIVOS:             ${activos:>12,.2f}",
        "",
        "── PASIVOS ─────────────────────────────────────",
        f"  Depósitos Clientes:        ${dep_clientes:>12,.2f}",
        f"  Cuentas de Ahorro:         ${ahorros:>12,.2f}",
        f"  Cuentas Corrientes:        ${corrientes:>12,.2f}",
        f"  Depósitos a Plazo Fijo:    ${plazos_fijos:>12,.2f}",
        f"  Obligaciones con Bancos:   ${oblig_bancos:>12,.2f}",
        f"  Impuestos por Pagar:       ${impuestos_pp:>12,.2f}",
        f"  ─────────────────────────────────────────────",
        f"  TOTAL PASIVOS:             ${pasivos:>12,.2f}",
        "",
        "── PATRIMONIO + RESULTADOS ──────────────────────",
        f"  Capital Banco:             ${capital:>12,.2f}",
        f"  Reservas Legales:          ${reservas:>12,.2f}",
        f"  Utilidades del Ejercicio:  ${utilidades:>12,.2f}",
        f"  Ingresos Intereses:        ${ing_int:>12,.2f}",
        f"  Ingresos Comisiones:       ${ing_com:>12,.2f}",
        f"  Ingresos Tarjeta Crédito:  ${ing_tc:>12,.2f}",
        f"  Ingresos por Mora:         ${ing_mora:>12,.2f}",
        f"  (-) Gastos Intereses:      ${-gast_int:>12,.2f}",
        f"  (-) Gastos Operativos:     ${-gast_oper:>12,.2f}",
        f"  (-) Gastos por Provisiones:${-gast_prov:>12,.2f}",
        f"  (-) Gastos por Mora Pagada:${-gast_mora:>12,.2f}",
        f"  ─────────────────────────────────────────────",
        f"  TOTAL PAT+RES:             ${patrimonio:>12,.2f}",
        f"  DIFERENCIA:                ${diff_bal:>12,.2f}",
        ("✅ BALANCE CUADRADO" if abs(diff_bal) < 0.01
         else f"  ❌ Balance descuadrado en ${diff_bal:,.2f}"),
        "",
        "═══════════════ LIBRO MAYOR ════════════════════",
        f"  Total Débitos:   ${float(total_deb):>12,.2f}",
        f"  Total Créditos:  ${float(total_cre):>12,.2f}",
        f"  Diferencia:      ${diff_libro:>12,.2f}",
        ("  ✅ LIBRO CUADRADO" if abs(diff_libro) < 0.01
         else f"  ❌ Libro descuadrado en ${diff_libro:,.2f}"),
        "",
        "═══════════════ DEPÓSITOS ══════════════════════",
        f"  Suma saldos clientes:      ${suma_clientes:>12,.2f}",
        f"  Dep. Clientes+Ahorro+Cte:  ${dep_total:>12,.2f}",
        f"  Diferencia:                ${diff_dep:>12,.2f}",
        ("  ✅ DEPÓSITOS CUADRAN" if abs(diff_dep) < 0.01
         else f"  ❌ Diferencia en depósitos ${diff_dep:,.2f}"),
        "",
        "═══════════════ CAJA ═══════════════════════════",
        f"  Caja Contable:   ${caja:>12,.2f}",
        "  ✅ CAJA CUADRA (viene del libro mayor)",
    ]

    if abs(diff_bal)   >= 0.01: errores += 1
    if abs(diff_libro) >= 0.01: errores += 1
    if abs(diff_dep)   >= 0.01: errores += 1

    return errores, lines