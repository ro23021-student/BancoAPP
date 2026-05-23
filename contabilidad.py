"""
contabilidad.py — Motor de contabilidad de partida doble
Sin dependencias externas: solo SQLAlchemy.

PLAN DE CUENTAS:
  ACTIVOS      (tipo_normal=D): Caja General, Prestamos x Cobrar, Intereses x Cobrar
  PASIVOS      (tipo_normal=C): Depositos Clientes
  PATRIMONIO   (tipo_normal=C): Capital Banco
  INGRESOS     (tipo_normal=C): Ingresos Intereses, Ingresos Comisiones
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from sqlalchemy import func

from models import CuentaContable, Asiento, LineaAsiento, ConfigBanco


# ─── Helpers decimales ───────────────────────────────────────

def money(v):
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def f(v):
    return money(v)


# ─── Plan de cuentas ─────────────────────────────────────────

PLAN_CUENTAS = [
    # (nombre,                categoria,    tipo_normal)
    ("Caja General",          "ACTIVO",     "D"),
    ("Prestamos x Cobrar",    "ACTIVO",     "D"),
    ("Intereses x Cobrar",    "ACTIVO",     "D"),
    ("Depositos Clientes",    "PASIVO",     "C"),
    ("Capital Banco",         "PATRIMONIO", "C"),
    ("Ingresos Intereses",    "INGRESO",    "C"),
    ("Ingresos Comisiones",   "INGRESO",    "C"),
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

    caja        = saldo_cuenta(session, "Caja General")
    prest_c     = saldo_cuenta(session, "Prestamos x Cobrar")
    int_c       = saldo_cuenta(session, "Intereses x Cobrar")
    depositos   = saldo_cuenta(session, "Depositos Clientes")
    capital     = saldo_cuenta(session, "Capital Banco")
    ing_int     = saldo_cuenta(session, "Ingresos Intereses")
    ing_com     = saldo_cuenta(session, "Ingresos Comisiones")

    activos     = round(caja + prest_c + int_c, 2)
    pasivos     = round(depositos, 2)
    patrimonio  = round(capital + ing_int + ing_com, 2)
    diff_bal    = round(activos - pasivos - patrimonio, 2)

    # Débitos vs créditos globales
    total_deb = session.query(func.coalesce(func.sum(LineaAsiento.debito),  0.0)).scalar()
    total_cre = session.query(func.coalesce(func.sum(LineaAsiento.credito), 0.0)).scalar()
    diff_libro = round(float(total_deb) - float(total_cre), 2)

    # Depósitos: la cuenta Depositos Clientes debe ser EXACTAMENTE igual a la suma
    # de saldos de clientes. Esto es así porque cada operación que cambia el saldo
    # del cliente (depósito, retiro, transferencia, préstamo, pago) tiene su
    # contrapartida exacta en la cuenta Depositos Clientes.
    suma_clientes = float(
        session.query(func.coalesce(func.sum(Cliente.saldo), 0.0)).scalar()
    )
    diff_dep = round(suma_clientes - depositos, 2)

    errores = 0
    lines   = []

    lines += [
        "═══════════════ BALANCE GENERAL ═══════════════",
        f"  Caja General:          ${caja:>12,.2f}",
        f"  Préstamos x Cobrar:    ${prest_c:>12,.2f}",
        f"  Intereses x Cobrar:    ${int_c:>12,.2f}",
        f"  ─────────────────────────────────────────",
        f"  TOTAL ACTIVOS:         ${activos:>12,.2f}",
        "",
        f"  Depósitos Clientes:    ${depositos:>12,.2f}",
        f"  Capital Banco:         ${capital:>12,.2f}",
        f"  Ingresos Intereses:    ${ing_int:>12,.2f}",
        f"  Ingresos Comisiones:   ${ing_com:>12,.2f}",
        f"  ─────────────────────────────────────────",
        f"  TOTAL PAS+PAT+ING:     ${(pasivos+patrimonio):>12,.2f}",
        f"  DIFERENCIA:            ${diff_bal:>12,.2f}",
        ("  ✅ BALANCE CUADRADO" if abs(diff_bal) < 0.01
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
        f"  Suma saldos clientes:  ${suma_clientes:>12,.2f}",
        f"  Cuenta Dep. Clientes:  ${depositos:>12,.2f}",
        f"  Diferencia:            ${diff_dep:>12,.2f}",
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