"""
operaciones.py — Lógica de negocio del banco.
Cada operación actualiza tanto los saldos operativos (clientes)
como el libro mayor contable, garantizando consistencia.
"""

from decimal import Decimal, ROUND_HALF_UP
MIN_TRANSACCION = Decimal("1.00")
from sqlalchemy import func
from models import Cliente, Movimiento, Prestamo
from contabilidad import money, f, registrar, saldo_cuenta, caja_real


# ─── Helpers ─────────────────────────────────────────────────

def _get_cliente(session, cid):
    return session.query(Cliente).filter_by(id=cid).first()

def _mov(session, cliente_id, tipo, monto, desc):
    session.add(Movimiento(
        cliente_id=cliente_id,
        tipo=tipo,
        monto=money(monto),
        descripcion=desc,
    ))
    session.flush()

def _set_saldo(session, cid, saldo):
    c = _get_cliente(session, cid)
    c.saldo = f(saldo)
    session.flush()


# ─── Clientes ────────────────────────────────────────────────

def crear_cliente(session, nombre, tipo, saldo_inicial):
    nombre = nombre.strip().title()
    if not nombre:
        return False, "Nombre inválido"
    if saldo_inicial < 0:
        return False, "El saldo inicial no puede ser negativo"
    if session.query(Cliente).filter_by(nombre=nombre).first():
        return False, f"Ya existe un cliente con el nombre '{nombre}'"

    cliente = Cliente(nombre=nombre, tipo=tipo, saldo=Decimal("0.00"))
    session.add(cliente)
    session.flush()   # para obtener el ID

    if saldo_inicial > 0:
        monto = f(saldo_inicial)
        # Efectivo entra a caja → se registra como depósito del cliente
        registrar(
            session,
            debitos=[("Caja General", monto)],
            creditos=[("Depositos Clientes", monto)],
            descripcion=f"Apertura cuenta — {nombre}",
        )
        _set_saldo(session, cliente.id, monto)
        _mov(session, cliente.id, "Apertura", monto, "Saldo inicial apertura")

    try:
        session.commit()
        return True, f"Cliente '{nombre}' creado con ID {cliente.id}"
    except Exception as e:
        session.rollback()
        return False, f"Error: {str(e)}"


# ─── Depósito ────────────────────────────────────────────────

def depositar(session, cliente_id, monto):
    """
    El cliente deposita efectivo en ventanilla.
    Se cobra 2% de comisión sobre el monto bruto.
    El neto (98%) se acredita al cliente; el 2% es ingreso del banco.

    Asiento:
      Débito  Caja General         monto_bruto
      Crédito Depositos Clientes   monto_neto
      Crédito Ingresos Comisiones  comision
    """
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    if money(monto) < MIN_TRANSACCION:
        return False, f"Monto mínimo permitido: ${MIN_TRANSACCION}"

    bruto    = money(monto)
    comision = money(bruto * Decimal("0.02"))
    neto     = money(bruto - comision)

    registrar(
    session,
    debitos=[("Caja General", bruto)],
    creditos=[
        ("Depositos Clientes", neto),
        ("Ingresos Comisiones", comision),
    ],
    descripcion=f"Depósito cliente ID {cliente_id}",
    )
    _set_saldo(session, cliente_id, money(cliente.saldo) + neto)
    _mov(session, cliente_id, "Deposito", money(neto),
         f"Depósito (bruto ${money(bruto):.2f}, comisión ${money(comision):.2f})")
    try:
        session.commit()
        return True, f"Depositado ${money(neto):.2f} (comisión ${money(comision):.2f})"
    except Exception as e:
        session.rollback()
        return False, f"Error al depositar: {str(e)}"
     

# ─── Retiro ──────────────────────────────────────────────────

def retirar(session, cliente_id, monto):
    """
    El cliente retira efectivo.

    Asiento:
      Débito  Depositos Clientes   monto
      Crédito Caja General         monto
    """
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    monto = money(monto)
    if monto < MIN_TRANSACCION:
        return False, "El monto debe ser mayor a cero"
    if monto > cliente.saldo:
        return False, f"Saldo insuficiente (disponible: ${cliente.saldo:,.2f})"

    registrar(
        session,
        debitos=[("Depositos Clientes", monto)],
        creditos=[("Caja General", monto)],
        descripcion=f"Retiro cliente ID {cliente_id}",
    )
    _set_saldo(session, cliente_id, cliente.saldo - monto)
    _mov(session, cliente_id, "Retiro", monto, "Retiro en ventanilla")
    try:
        session.commit()
        return True, f"Retiro de ${monto:,.2f} realizado"
    except Exception as e:
        session.rollback()
        return False, str(e)


# ─── Transferencia ───────────────────────────────────────────

def transferir(session, origen_id, destino_id, monto):
    """
    Transferencia entre dos clientes. Comisión 1% al origen.
    El monto llega completo al destino; el origen paga monto + comisión.

    Asiento:
      Débito  Depositos Clientes   monto + comision   (sale del pasivo del origen)
      Crédito Depositos Clientes   monto              (entra al pasivo del destino)
      Crédito Ingresos Comisiones  comision
    """
    if origen_id == destino_id:
        return False, "No puedes transferirte a ti mismo"
    co = _get_cliente(session, origen_id)
    cd = _get_cliente(session, destino_id)
    if not co:
        return False, "Cliente origen no existe"
    if not cd:
        return False, "Cliente destino no existe"
    monto = money(monto)
    if money(monto) < MIN_TRANSACCION:
        return False, "El monto debe ser mayor a cero"

    comision = money(monto * Decimal("0.01"))
    total    = money(monto + comision)

    if money(total) > co.saldo:
        return False, f"Saldo insuficiente (necesita ${money(total):.2f}, tiene ${co.saldo:,.2f})"

    # Un único asiento cuadrado:
    registrar(
        session,
        debitos=[("Depositos Clientes", money(total))],
        creditos=[
            ("Depositos Clientes",  money(monto)),
            ("Ingresos Comisiones", money(comision)),
        ],
        descripcion=f"Transferencia {origen_id} → {destino_id}",
    )
    _set_saldo(session, origen_id,  co.saldo - money(total))
    _set_saldo(session, destino_id, cd.saldo + money(monto))
    _mov(session, origen_id,  "Transferencia Enviada",   money(monto),
         f"Envío a {cd.nombre} (comisión ${money(comision):.2f})")
    _mov(session, destino_id, "Transferencia Recibida",  money(monto),
         f"Recibido de {co.nombre}")
    try:
        session.commit()
        return True, (f"Transferencia de ${money(monto):.2f} realizada "
                      f"(comisión ${money(comision):.2f})")
    except Exception as e:
        session.rollback()
        return False, str(e)


# ─── Préstamos ───────────────────────────────────────────────

def otorgar_prestamo(session, cliente_id, monto):
    """
    El banco otorga un préstamo al cliente (creación de dinero bancario).
    El interés total es 10% del capital.
    El dinero del préstamo se acredita en la cuenta del cliente.

    Asiento:
      Débito  Préstamos x Cobrar   monto   ← se crea el activo
      Crédito Depósitos Clientes   monto   ← queda en cuenta del cliente
    (El dinero no sale de Caja; el banco crea el crédito respaldado por su capital)
    """
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    monto = money(monto)
    if monto <= 0:
        return False, "El monto debe ser mayor a cero"
    if monto < 100:
        return False, "El monto mínimo de préstamo es $100"

    # Verificar que el banco tiene respaldo patrimonial suficiente
    # (el préstamo crea dinero pero debe estar respaldado por el capital del banco)
    capital = money(saldo_cuenta(session, "Capital Banco"))
    total_prestamos = money(
        session.query(func.coalesce(func.sum(Prestamo.saldo_pendiente), 0.0))
        .filter(Prestamo.estado == "ACTIVO")
        .scalar()
    )
    capacidad = capital - total_prestamos
    if capacidad < monto:
        return False, (f"Sin capacidad de crédito suficiente "
                       f"(capital: ${capital:,.2f}, préstamos activos: ${total_prestamos:,.2f})")

    interes = f(monto * Decimal("0.10"))

    p = Prestamo(
        cliente_id=cliente_id,
        monto=monto,
        interes=interes,
        interes_devengado=0.0,
        interes_pagado=0.0,
        saldo_pendiente=monto,
        estado="ACTIVO",
    )
    session.add(p)
    session.flush()

    registrar(
        session,
        debitos=[("Prestamos x Cobrar", monto)],
        creditos=[("Depositos Clientes", monto)],
        descripcion=f"Préstamo otorgado cliente ID {cliente_id}",
    )
    # El dinero del préstamo llega al saldo del cliente (como si lo depositara)
    _set_saldo(session, cliente_id, cliente.saldo + monto)
    _mov(session, cliente_id, "Prestamo", monto,
         f"Préstamo recibido (interés total: ${interes:.2f})")
    try:
        session.commit()
        return True, (f"Préstamo de ${monto:,.2f} otorgado "
                      f"(interés ${interes:.2f} — deuda total ${monto+interes:,.2f})")
    except Exception as e:
        session.rollback()
        return False, f"Error al otorgar préstamo: {str(e)}"


def pagar_prestamo(session, cliente_id, monto_pago):
    """
    El cliente paga (parcial o total) su préstamo activo más antiguo.
    El pago se aplica primero a intereses devengados, luego a capital.

    Asientos según composición del pago:
      Si hay capital:
        Débito  Depositos Clientes   capital
        Crédito Prestamos x Cobrar   capital

      Si hay interés devengado (ya registrado como x cobrar):
        Débito  Depositos Clientes   interes
        Crédito Intereses x Cobrar   interes   ← cancela el activo devengado

      Si hay interés no devengado (pago adelantado):
        Débito  Depositos Clientes   interes
        Crédito Ingresos Intereses   interes   ← ingreso directo
    """
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"

    monto_pago = f(monto_pago)
    if monto_pago <= 0:
        return False, "El monto debe ser mayor a cero"

    # Préstamo activo más antiguo
    p = (session.query(Prestamo)
         .filter_by(cliente_id=cliente_id, estado="ACTIVO")
         .filter(Prestamo.saldo_pendiente > 0)
         .order_by(Prestamo.fecha)
         .first())
    if not p:
        return False, "No hay préstamo activo con saldo pendiente"

    interes_pendiente = round(p.interes - p.interes_pagado, 2)
    deuda_total       = round(p.saldo_pendiente + interes_pendiente, 2)

    if monto_pago > cliente.saldo:
        return False, f"Saldo insuficiente (tiene ${cliente.saldo:,.2f})"
    if monto_pago > deuda_total:
        return False, (f"El pago supera la deuda total "
                       f"(${deuda_total:.2f} = capital ${p.saldo_pendiente:.2f} + "
                       f"interés ${interes_pendiente:.2f})")

    # Distribuir pago: primero intereses, luego capital
    pago_interes = f(min(monto_pago, interes_pendiente))
    pago_capital = f(monto_pago - pago_interes)

    # Ajuste: si el capital calculado supera el saldo pendiente (redondeo)
    if pago_capital > p.saldo_pendiente:
        exceso       = f(pago_capital - p.saldo_pendiente)
        pago_capital = f(p.saldo_pendiente)
        pago_interes = f(pago_interes + exceso)

    nuevo_saldo = max(0.0, round(p.saldo_pendiente - pago_capital, 2))

    # ─ Asiento capital ─
    # El pago sale del saldo del cliente (pasivo Depositos Clientes) y cancela el activo
    if pago_capital > Decimal("0.005"):
        registrar(
            session,
            debitos=[("Depositos Clientes", pago_capital)],
            creditos=[("Prestamos x Cobrar", pago_capital)],
            descripcion=f"Pago capital préstamo ID {p.id}",
        )

    # ─ Asiento intereses ─
    if pago_interes > Decimal("0.005"):
        devengado_contable = money(saldo_cuenta(session, "Intereses x Cobrar"))
        desde_devengado    = money(min(pago_interes, devengado_contable))
        resto_interes      = money(pago_interes - desde_devengado)

        if desde_devengado > Decimal("0.005"):
            registrar(
                session,
                debitos=[("Depositos Clientes", desde_devengado)],
                creditos=[("Intereses x Cobrar", desde_devengado)],
                descripcion=f"Cobro interés devengado préstamo ID {p.id}",
            )
        if resto_interes > Decimal("0.005"):
            registrar(
                session,
                debitos=[("Depositos Clientes", resto_interes)],
                creditos=[("Ingresos Intereses", resto_interes)],
                descripcion=f"Cobro interés directo préstamo ID {p.id}",
            )

    # Actualizar préstamo
    p.saldo_pendiente   = nuevo_saldo
    p.interes_pagado    = round(p.interes_pagado + pago_interes, 2)
    p.interes_devengado = max(0.0, round(p.interes_devengado - pago_interes, 2))
    if nuevo_saldo < Decimal("0.005"):
        p.estado = "PAGADO"
    session.flush()

    # Descontar del saldo del cliente
    _set_saldo(session, cliente_id, cliente.saldo - monto_pago)
    _mov(session, cliente_id, "Pago Prestamo", monto_pago,
         f"Pago préstamo (capital ${pago_capital:.2f} + interés ${pago_interes:.2f})")
    session.commit()
    return True, (f"Pago de ${monto_pago:.2f} registrado "
                  f"(capital ${pago_capital:.2f} + interés ${pago_interes:.2f})")


# ─── Devengo de intereses ────────────────────────────────────

def devengar_interes(session):
    """
    Registra una cuota mensual de interés (interés/12) para cada
    préstamo activo que aún no haya devengado su interés completo.

    Asiento por cada préstamo:
      Débito  Intereses x Cobrar   cuota_mensual
      Crédito Ingresos Intereses   cuota_mensual
    """
    prestamos = (session.query(Prestamo)
                 .filter_by(estado="ACTIVO")
                 .filter(Prestamo.saldo_pendiente > 0)
                 .all())
    if not prestamos:
        return False, "No hay préstamos activos"

    total = Decimal("0.00")
    procesados = 0
    for p in prestamos:
        restante = money(p.interes - p.interes_devengado)
        if restante <= Decimal("0.005"):
            continue
        cuota = f(min(p.interes / Decimal("12"), restante))
        registrar(
            session,
            debitos=[("Intereses x Cobrar", cuota)],
            creditos=[("Ingresos Intereses", cuota)],
            descripcion=f"Devengo interés mensual — préstamo ID {p.id}",
        )
        p.interes_devengado = round(p.interes_devengado + cuota, 2)
        total += cuota
        procesados += 1

    if procesados == 0:
        return False, "Todos los intereses ya fueron devengados"
    try:
        session.commit()
        return True, f"Devengado ${total:.2f} en {procesados} préstamo(s)"
    except Exception as e:
        session.rollback()
        return False, f"Error al devengar intereses: {str(e)}"