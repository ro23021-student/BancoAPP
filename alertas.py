"""
alertas.py - Sistema de alertas del banco.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from tiempo import hoy as _hoy
from sqlalchemy import func, text
from models import Cliente, Prestamo, AuditLog, ConfigBanco


DEFAULTS = {
    "alerta_saldo_minimo":     "50.00",
    "alerta_caja_minima":      "1000.00",
    "alerta_dias_vencimiento": "7",
    "alerta_intentos_login":   "3",
    "tasa_mora":               "0.02",
}


def _cfg(session, clave):
    r = session.query(ConfigBanco).filter_by(clave=clave).first()
    return r.valor if r else DEFAULTS.get(clave, "0")


TIPO_SALDO_BAJO      = "saldo_bajo"
TIPO_PRESTAMO_VENCE  = "prestamo_vence"
TIPO_CAJA_BAJA       = "caja_baja"
TIPO_LOGIN_FALLIDO   = "login_fallido"
TIPO_PRESTAMO_VENCIDO= "prestamo_vencido"
TIPO_MORA_ACTIVA     = "mora_activa"

NIVEL_INFO    = "info"
NIVEL_WARNING = "warning"
NIVEL_ERROR   = "error"


def _alerta(tipo, nivel, titulo, detalle, extra=None):
    return {
        "tipo":    tipo,
        "nivel":   nivel,
        "titulo":  titulo,
        "detalle": detalle,
        "extra":   extra or {},
        "ts":      datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def verificar_saldos_bajos(session):
    minimo = Decimal(_cfg(session, "alerta_saldo_minimo"))
    clientes = (session.query(Cliente)
                .filter(Cliente.estado == "ACTIVO")
                .filter(Cliente.saldo < minimo)
                .order_by(Cliente.saldo)
                .all())
    alertas = []
    for c in clientes:
        alertas.append(_alerta(
            TIPO_SALDO_BAJO, NIVEL_WARNING,
            f"Saldo bajo - {c.nombre}",
            f"Saldo actual: ${float(c.saldo):,.2f} (minimo: ${float(minimo):,.2f})",
            {"cliente_id": c.id, "nombre": c.nombre, "saldo": float(c.saldo)},
        ))
    return alertas


def verificar_prestamos_por_vencer(session):
    dias = int(_cfg(session, "alerta_dias_vencimiento"))
    hoy  = _hoy()
    lim  = hoy + timedelta(days=dias)

    prestamos = (session.query(Prestamo)
                 .filter(Prestamo.estado == "ACTIVO")
                 .filter(Prestamo.saldo_pendiente > 0)
                 .filter(Prestamo.fecha_vencimiento != None)
                 .filter(Prestamo.fecha_vencimiento <= lim)
                 .filter(Prestamo.fecha_vencimiento >= hoy)
                 .all())
    alertas = []
    for p in prestamos:
        dias_rest = (p.fecha_vencimiento - hoy).days
        alertas.append(_alerta(
            TIPO_PRESTAMO_VENCE, NIVEL_WARNING,
            f"Prestamo proximo a vencer - {p.cliente.nombre}",
            f"Vence el {p.fecha_vencimiento} ({dias_rest} dias). Pendiente: ${float(p.saldo_pendiente):,.2f}",
            {"prestamo_id": p.id, "cliente": p.cliente.nombre,
             "dias_rest": dias_rest, "saldo": float(p.saldo_pendiente)},
        ))
    return alertas


def verificar_prestamos_vencidos(session):
    hoy = _hoy()
    prestamos = (session.query(Prestamo)
                 .filter(Prestamo.estado == "ACTIVO")
                 .filter(Prestamo.saldo_pendiente > 0)
                 .filter(Prestamo.fecha_vencimiento != None)
                 .filter(Prestamo.fecha_vencimiento < hoy)
                 .all())
    alertas = []
    for p in prestamos:
        dias_mora = (hoy - p.fecha_vencimiento).days
        alertas.append(_alerta(
            TIPO_PRESTAMO_VENCIDO, NIVEL_ERROR,
            f"Prestamo VENCIDO - {p.cliente.nombre}",
            f"Vencio el {p.fecha_vencimiento} (hace {dias_mora} dias). Pendiente: ${float(p.saldo_pendiente):,.2f}",
            {"prestamo_id": p.id, "cliente": p.cliente.nombre,
             "dias_mora": dias_mora, "saldo": float(p.saldo_pendiente)},
        ))
    return alertas


def verificar_mora_activa(session):
    """Prestamos con mora > 0. Usa SQL directo para evitar cache ORM."""
    rows = session.execute(text("""
        SELECT p.id, p.mora_acumulada, p.dias_mora, p.saldo_pendiente,
               c.nombre as cliente_nombre
        FROM prestamos p
        JOIN clientes c ON c.id = p.cliente_id
        WHERE p.estado = 'ACTIVO'
          AND CAST(p.mora_acumulada AS REAL) > 0.0
    """)).fetchall()

    alertas = []
    for row in rows:
        alertas.append(_alerta(
            TIPO_MORA_ACTIVA, NIVEL_WARNING,
            f"Mora activa - {row.cliente_nombre}",
            f"Mora: ${float(row.mora_acumulada):,.2f} ({row.dias_mora or 0} dias). Pendiente: ${float(row.saldo_pendiente):,.2f}",
            {"prestamo_id": row.id, "cliente": row.cliente_nombre,
             "mora": float(row.mora_acumulada)},
        ))
    return alertas


def verificar_caja_baja(session, caja_actual):
    minimo = float(_cfg(session, "alerta_caja_minima"))
    if caja_actual < minimo:
        return [_alerta(
            TIPO_CAJA_BAJA, NIVEL_ERROR,
            "Caja por debajo del minimo",
            f"Caja actual: ${caja_actual:,.2f} (minimo: ${minimo:,.2f}).",
            {"caja": caja_actual, "minimo": minimo},
        )]
    return []


def verificar_intentos_login(session):
    umbral  = int(_cfg(session, "alerta_intentos_login"))
    ventana = datetime.utcnow() - timedelta(minutes=15)

    logs = (session.query(AuditLog.username, func.count(AuditLog.id).label("intentos"))
            .filter(AuditLog.accion == "LOGIN_FALLIDO")
            .filter(AuditLog.fecha >= ventana)
            .group_by(AuditLog.username)
            .having(func.count(AuditLog.id) >= umbral)
            .all())

    alertas = []
    for username, intentos in logs:
        alertas.append(_alerta(
            TIPO_LOGIN_FALLIDO, NIVEL_ERROR,
            f"Intentos de acceso fallidos - {username}",
            f"{intentos} intento(s) fallidos en los ultimos 15 minutos.",
            {"username": username, "intentos": intentos},
        ))
    return alertas


def obtener_todas_alertas(session, caja_actual):
    todas = []
    todas += verificar_caja_baja(session, caja_actual)
    todas += verificar_intentos_login(session)
    todas += verificar_prestamos_vencidos(session)
    todas += verificar_mora_activa(session)
    todas += verificar_prestamos_por_vencer(session)
    todas += verificar_saldos_bajos(session)
    orden = {NIVEL_ERROR: 0, NIVEL_WARNING: 1, NIVEL_INFO: 2}
    todas.sort(key=lambda a: orden.get(a["nivel"], 3))
    return todas


def contar_alertas(session, caja_actual):
    todas = obtener_todas_alertas(session, caja_actual)
    return {
        "total":   len(todas),
        "error":   sum(1 for a in todas if a["nivel"] == NIVEL_ERROR),
        "warning": sum(1 for a in todas if a["nivel"] == NIVEL_WARNING),
        "info":    sum(1 for a in todas if a["nivel"] == NIVEL_INFO),
    }


def calcular_mora(session):
    """
    Recalcula mora usando UPDATE SQL directo (evita cache ORM de SQLAlchemy).
    Tabla: prestamos (con s).
    """
    tasa_mora_mensual = float(_cfg(session, "tasa_mora") or "0.02")
    hoy = _hoy()

    prestamos = (session.query(Prestamo)
                 .filter(Prestamo.estado == "ACTIVO")
                 .filter(Prestamo.saldo_pendiente > 0)
                 .filter(Prestamo.fecha_vencimiento != None)
                 .filter(Prestamo.fecha_vencimiento < hoy)
                 .all())

    if not prestamos:
        return 0, "No hay prestamos vencidos"

    actualizados = 0
    try:
        for p in prestamos:
            dias_mora  = (hoy - p.fecha_vencimiento).days
            saldo      = float(p.saldo_pendiente)
            mora_total = round(saldo * tasa_mora_mensual / 30.0 * dias_mora, 2)

            session.execute(
                text("UPDATE prestamos SET mora_acumulada = :mora, dias_mora = :dias WHERE id = :pid"),
                {"mora": mora_total, "dias": int(dias_mora), "pid": p.id}
            )
            actualizados += 1

        session.commit()
        session.expire_all()

    except Exception as e:
        session.rollback()
        return 0, f"Error al calcular mora: {e}"

    return actualizados, f"Mora actualizada para {actualizados} prestamo(s)"
