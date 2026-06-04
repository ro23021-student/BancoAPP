"""
operaciones.py — Lógica de negocio del banco.
Cada operación actualiza tanto los saldos operativos (clientes)
como el libro mayor contable, garantizando consistencia.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
MIN_TRANSACCION = Decimal("1.00")
from sqlalchemy import func
from models import Cliente, CuotaPrestamo, Movimiento, Prestamo, ConfigBanco
from contabilidad import money, f, registrar, saldo_cuenta, caja_real


# ─── Tasas configurables (desde ConfigBanco) ─────────────────

def _tasa(session, clave: str, default: str) -> Decimal:
    """Lee una tasa de ConfigBanco o devuelve el default hardcodeado."""
    r = session.query(ConfigBanco).filter_by(clave=clave).first()
    return Decimal(r.valor if r else default)


def tasa_deposito(session) -> Decimal:
    return _tasa(session, "tasa_deposito", "0.02")


def tasa_transferencia(session) -> Decimal:
    return _tasa(session, "tasa_transferencia", "0.01")


def tasa_prestamo(session) -> Decimal:
    return _tasa(session, "tasa_prestamo", "0.10")


# ─── Helpers ─────────────────────────────────────────────────

def _get_cliente(session, cid):
    return session.query(Cliente).filter_by(id=cid).first()

def _mov(session, cliente_id, tipo, monto, desc):
    from models import _gen_num_trx
    session.add(Movimiento(
        num_trx=_gen_num_trx(),
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

def calcular_cuota(monto, tasa_anual, meses):
    """
    Calcula la cuota mensual con amortización francesa (interés compuesto).
    Si la tasa es 0, retorna simplemente capital / meses.
    """
    if meses <= 0:
        raise ValueError("El plazo debe ser mayor a cero")
    tasa = Decimal(str(tasa_anual)) / Decimal("12")
    if tasa == 0:
        return money(Decimal(str(monto)) / Decimal(str(meses)))

    cuota = (
        monto *
        (tasa * (1 + tasa) ** meses)
        /
        ((1 + tasa) ** meses - 1)
    )

    return money(cuota)


# ─── Clientes ────────────────────────────────────────────────

def crear_cliente(session, nombre, tipo, saldo_inicial,
                   documento=None, tipo_documento="DUI",
                   telefono=None, email=None,
                   direccion=None, fecha_nacimiento=None,
                   nit=None, profesion=None, ingresos_mensuales=0,
                   tipo_cuenta_id=None, sucursal_id=None):
    nombre = nombre.strip().title()
    if not nombre:
        return False, "Nombre inválido"
    if saldo_inicial < 0:
        return False, "El saldo inicial no puede ser negativo"
    if session.query(Cliente).filter_by(nombre=nombre).first():
        return False, f"Ya existe un cliente con el nombre '{nombre}'"

    if documento:
        documento = documento.strip()
        dup = session.query(Cliente).filter_by(documento=documento).first()
        if dup:
            return False, f"Ya existe un cliente con ese documento ({documento})"

    if email:
        email = email.strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            return False, "El formato del email no es válido"

    cliente = Cliente(
        nombre=nombre,
        tipo=tipo,
        tipo_cuenta_id=tipo_cuenta_id,
        sucursal_id=sucursal_id,
        saldo=Decimal("0.00"),
        documento=documento or None,
        tipo_documento=tipo_documento,
        telefono=telefono.strip() if telefono else None,
        email=email.strip().lower() if email else None,
        direccion=direccion.strip() if direccion else None,
        fecha_nacimiento=fecha_nacimiento or None,
        nit=nit or None,
        profesion=profesion.strip() if profesion else None,
        ingresos_mensuales=Decimal(str(ingresos_mensuales)) if ingresos_mensuales else Decimal("0"),
        kyc_completo=bool(documento and nit),
        estado="ACTIVO",
    )
    session.add(cliente)
    session.flush()

    if saldo_inicial > 0:
        monto = f(saldo_inicial)
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
        return True, f"Cliente '{nombre}' creado — Cuenta: {cliente.num_cuenta}"
    except Exception as e:
        session.rollback()
        return False, f"Error: {str(e)}"


# ─── Editar cliente ───────────────────────────────────────────

def editar_cliente(session, cliente_id, **kwargs):
    """Actualiza datos personales de un cliente (no saldo ni estado)."""
    from datetime import datetime as _dt
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    if cliente.estado == "CERRADO":
        return False, "No se puede editar una cuenta cerrada"

    campos_permitidos = {
        "nombre", "tipo", "documento", "tipo_documento",
        "telefono", "email", "direccion", "fecha_nacimiento",
    }
    for campo, valor in kwargs.items():
        if campo not in campos_permitidos:
            continue
        if isinstance(valor, str):
            valor = valor.strip() or None
        if campo == "nombre" and valor:
            valor = valor.title()
            dup = (session.query(Cliente)
                   .filter(Cliente.nombre == valor, Cliente.id != cliente_id)
                   .first())
            if dup:
                return False, f"Ya existe otro cliente con el nombre '{valor}'"
        if campo == "documento" and valor:
            dup = (session.query(Cliente)
                   .filter(Cliente.documento == valor, Cliente.id != cliente_id)
                   .first())
            if dup:
                return False, "Ese documento ya está registrado en otra cuenta"
        if campo == "email" and valor:
            valor = valor.lower()
            if "@" not in valor or "." not in valor.split("@")[-1]:
                return False, "El formato del email no es válido"
        setattr(cliente, campo, valor)

    cliente.actualizado_en = _dt.utcnow()
    try:
        session.commit()
        return True, f"Datos de '{cliente.nombre}' actualizados correctamente"
    except Exception as e:
        session.rollback()
        return False, f"Error al actualizar: {str(e)}"


# ─── Suspender / reactivar ────────────────────────────────────

def suspender_cliente(session, cliente_id, motivo=""):
    """Suspende una cuenta ACTIVA (bloquea operaciones)."""
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    if cliente.estado == "CERRADO":
        return False, "La cuenta ya está cerrada"
    if cliente.estado == "SUSPENDIDO":
        return False, "La cuenta ya está suspendida"
    cliente.estado = "SUSPENDIDO"
    cliente.motivo_cierre = motivo.strip() if motivo else "Suspendida por administración"
    try:
        session.commit()
        return True, f"Cuenta '{cliente.nombre}' suspendida"
    except Exception as e:
        session.rollback()
        return False, str(e)


def reactivar_cliente(session, cliente_id):
    """Reactiva una cuenta SUSPENDIDA."""
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    if cliente.estado == "CERRADO":
        return False, "No se puede reactivar una cuenta cerrada"
    if cliente.estado == "ACTIVO":
        return False, "La cuenta ya está activa"
    cliente.estado = "ACTIVO"
    cliente.motivo_cierre = None
    try:
        session.commit()
        return True, f"Cuenta '{cliente.nombre}' reactivada"
    except Exception as e:
        session.rollback()
        return False, str(e)


# ─── Cerrar cuenta ────────────────────────────────────────────

def cerrar_cuenta(session, cliente_id, motivo=""):
    """
    Cierra permanentemente una cuenta.
    Requiere: saldo = 0 y sin préstamos activos.
    """
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"
    if cliente.estado == "CERRADO":
        return False, "La cuenta ya está cerrada"
    if float(cliente.saldo) > 0.005:
        return False, (
            f"No se puede cerrar: saldo pendiente de ${cliente.saldo:,.2f}. "
            "El cliente debe retirar su saldo antes de cerrar."
        )
    prestamos_activos = (
        session.query(Prestamo)
        .filter_by(cliente_id=cliente_id, estado="ACTIVO")
        .filter(Prestamo.saldo_pendiente > 0)
        .count()
    )
    if prestamos_activos > 0:
        return False, f"No se puede cerrar: hay {prestamos_activos} préstamo(s) activo(s) pendiente(s)."

    cliente.estado = "CERRADO"
    cliente.motivo_cierre = motivo.strip() if motivo else "Cierre solicitado"
    _mov(session, cliente_id, "Cierre", Decimal("0.00"),
         f"Cuenta cerrada — {cliente.motivo_cierre}")
    try:
        session.commit()
        return True, f"Cuenta '{cliente.nombre}' ({cliente.num_cuenta}) cerrada definitivamente"
    except Exception as e:
        session.rollback()
        return False, str(e)


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
    if cliente.estado != "ACTIVO":
        return False, f"Cuenta {cliente.estado.lower()} — operación no permitida"
    if money(monto) < MIN_TRANSACCION:
        return False, f"Monto mínimo permitido: ${MIN_TRANSACCION}"

    bruto    = money(monto)
    comision = money(bruto * tasa_deposito(session))
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
    if cliente.estado != "ACTIVO":
        return False, f"Cuenta {cliente.estado.lower()} — operación no permitida"
    monto = money(monto)
    if monto < MIN_TRANSACCION:
        return False, "El monto debe ser mayor a cero"
    if monto > cliente.saldo:
        return False, f"Saldo insuficiente (disponible: ${cliente.saldo:,.2f})"

    ok_lim, msg_lim = verificar_limite_diario(session, cliente_id, "Retiro", monto)
    if not ok_lim:
        return False, msg_lim

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
    if co.estado != "ACTIVO":
        return False, f"Cuenta origen {co.estado.lower()} — operación no permitida"
    if cd.estado == "CERRADO":
        return False, "La cuenta destino está cerrada — no puede recibir fondos"
    if cd.estado == "SUSPENDIDO":
        return False, "La cuenta destino está suspendida — no puede recibir fondos"
    monto = money(monto)
    if money(monto) < MIN_TRANSACCION:
        return False, "El monto debe ser mayor a cero"

    comision = money(monto * tasa_transferencia(session))
    total    = money(monto + comision)

    if money(total) > co.saldo:
        return False, f"Saldo insuficiente (necesita ${money(total):.2f}, tiene ${co.saldo:,.2f})"

    ok_lim, msg_lim = verificar_limite_diario(session, origen_id, "Transferencia Enviada", monto)
    if not ok_lim:
        return False, msg_lim

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

def otorgar_prestamo(session, cliente_id, monto, plazo_meses):
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
    if not isinstance(plazo_meses, int) or plazo_meses <= 0:
        return False, "El plazo debe ser un número entero mayor a cero"

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

    interes = f(monto * tasa_prestamo(session))

    cuota_mensual = f(
        (monto + interes) / plazo_meses
    )

    from datetime import timedelta

    p = Prestamo(
        cliente_id=cliente_id,
        monto=monto,
        interes=interes,
        interes_devengado=0,
        interes_pagado=0,
        saldo_pendiente=monto,
        estado="ACTIVO",

        plazo_meses=plazo_meses,

        cuota_mensual=cuota_mensual,

        fecha_vencimiento=
            datetime.utcnow().date()
            + timedelta(days=plazo_meses*30)
    )
    session.add(p)
    session.flush()

    saldo = monto

    capital_por_cuota = f(
        monto / plazo_meses
    )

    interes_por_cuota = f(
        interes / plazo_meses
    )

    for n in range(1, plazo_meses + 1):

        saldo = max(
            0,
            saldo - capital_por_cuota
        )

        cuota = CuotaPrestamo(

            prestamo_id=p.id,

            numero_cuota=n,

            fecha_vencimiento=
                datetime.utcnow().date()
                + timedelta(days=n*30),

            monto_cuota=
                capital_por_cuota +
                interes_por_cuota,

            capital=capital_por_cuota,

            interes=interes_por_cuota,

            saldo_restante=saldo
        )

        session.add(cuota)

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
    try:
        session.commit()
        return True, (f"Pago de ${monto_pago:.2f} registrado "
                      f"(capital ${pago_capital:.2f} + interés ${pago_interes:.2f})")
    except Exception as e:
        session.rollback()
        return False, f"Error al registrar pago: {str(e)}"


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

# ─── Reversión de operaciones ────────────────────────────────

OPERACIONES_REVERSIBLES = {"Deposito", "Retiro", "Transferencia Enviada"}
VENTANA_REVERSION_MIN   = 60   # solo reversible dentro de los últimos 60 minutos

def revertir_operacion(session, movimiento_id, motivo=""):
    """
    Revierte un depósito o retiro reciente (máx 60 min).
    Genera el asiento contable inverso y un movimiento de tipo 'Reverso'.
    No se puede revertir pagos de préstamos ni aperturas.
    """
    from datetime import timedelta
    mov = session.query(Movimiento).filter_by(id=movimiento_id).first()
    if not mov:
        return False, "Movimiento no encontrado"

    if mov.tipo not in OPERACIONES_REVERSIBLES:
        return False, (f"El tipo '{mov.tipo}' no es reversible. "
                       f"Solo se pueden revertir: {', '.join(OPERACIONES_REVERSIBLES)}.")

    limite = datetime.utcnow() - timedelta(minutes=VENTANA_REVERSION_MIN)
    if mov.fecha < limite:
        return False, (f"Solo se pueden revertir operaciones de los últimos "
                       f"{VENTANA_REVERSION_MIN} minutos.")

    # Verificar que no haya sido revertido ya
    ya_revertido = session.query(Movimiento).filter(
        Movimiento.tipo == "Reverso",
        Movimiento.descripcion.like(f"REVERSO mov#{movimiento_id}%"),
        Movimiento.cliente_id == mov.cliente_id,
    ).first()
    if ya_revertido:
        return False, "Esta operación ya fue revertida anteriormente."

    cliente = _get_cliente(session, mov.cliente_id)
    if not cliente:
        return False, "Cliente no encontrado"

    monto = money(mov.monto)

    try:
        if mov.tipo == "Deposito":
            # El depósito original acreditó neto al cliente y tomó comisión.
            # La reversión devuelve exactamente el neto al banco.
            if monto > cliente.saldo:
                return False, (f"Saldo insuficiente para revertir "
                               f"(cliente tiene ${cliente.saldo:,.2f}, "
                               f"reversión requiere ${monto:,.2f}).")
            registrar(
                session,
                debitos=[(  "Depositos Clientes", monto)],
                creditos=[( "Caja General",        monto)],
                descripcion=f"Reverso depósito mov#{movimiento_id}",
            )
            _set_saldo(session, cliente.id, cliente.saldo - monto)

        elif mov.tipo == "Retiro":
            registrar(
                session,
                debitos=[(  "Caja General",        monto)],
                creditos=[( "Depositos Clientes",  monto)],
                descripcion=f"Reverso retiro mov#{movimiento_id}",
            )
            _set_saldo(session, cliente.id, cliente.saldo + monto)

        elif mov.tipo == "Transferencia Enviada":
            # Buscar el movimiento de recepción correspondiente (mismo monto, misma fecha ≈)
            from datetime import timedelta as td
            recibido = (session.query(Movimiento)
                        .filter_by(tipo="Transferencia Recibida", monto=monto)
                        .filter(Movimiento.fecha >= mov.fecha - td(seconds=5))
                        .filter(Movimiento.fecha <= mov.fecha + td(seconds=5))
                        .first())
            if not recibido:
                return False, "No se encontró el movimiento receptor de la transferencia."
            dest = _get_cliente(session, recibido.cliente_id)
            if not dest:
                return False, "Cliente destino no encontrado."
            if monto > dest.saldo:
                return False, (f"El cliente destino ya no tiene fondos suficientes "
                               f"para revertir la transferencia.")
            registrar(
                session,
                debitos=[(  "Depositos Clientes", monto)],   # sale del destino
                creditos=[( "Depositos Clientes", monto)],   # vuelve al origen
                descripcion=f"Reverso transferencia mov#{movimiento_id}",
            )
            _set_saldo(session, cliente.id,  cliente.saldo + monto)
            _set_saldo(session, dest.id,     dest.saldo - monto)
            _mov(session, dest.id, "Reverso", monto,
                 f"REVERSO mov#{movimiento_id} — {motivo or 'Reversión administrativa'}")

        _mov(session, cliente.id, "Reverso", monto,
             f"REVERSO mov#{movimiento_id} — {motivo or 'Reversión administrativa'}")
        session.commit()
        return True, f"Operación #{movimiento_id} revertida correctamente."

    except Exception as e:
        session.rollback()
        return False, f"Error al revertir: {str(e)}"


# ─── Límites diarios por cliente ────────────────────────────

def _total_operado_hoy(session, cliente_id, tipo):
    """Suma de montos del tipo dado para el cliente en el día de hoy (UTC)."""
    from datetime import date
    hoy_inicio = datetime.combine(date.today(), datetime.min.time())
    total = session.query(func.coalesce(func.sum(Movimiento.monto), 0))\
        .filter_by(cliente_id=cliente_id, tipo=tipo)\
        .filter(Movimiento.fecha >= hoy_inicio)\
        .scalar()
    return money(total)


def limite_diario(session, clave):
    """Lee el límite diario desde ConfigBanco."""
    return _tasa(session, clave, "0") * Decimal("1")   # ya es monto, no porcentaje


def verificar_limite_diario(session, cliente_id, tipo_mov, monto_nuevo):
    """
    Verifica que el monto no supere el límite diario configurado.
    Retorna (True, "") si está dentro del límite, (False, msg) si lo supera.
    Claves en ConfigBanco: limite_retiro_diario, limite_transferencia_diaria.
    """
    CLAVES = {
        "Retiro":               ("limite_retiro_diario",        "5000.00"),
        "Transferencia Enviada":("limite_transferencia_diaria", "10000.00"),
    }
    if tipo_mov not in CLAVES:
        return True, ""

    clave, default = CLAVES[tipo_mov]
    limite = _tasa(session, clave, default)
    if limite <= 0:
        return True, ""   # 0 = sin límite

    ya_operado = _total_operado_hoy(session, cliente_id, tipo_mov)
    if ya_operado + money(monto_nuevo) > limite:
        return False, (f"Límite diario de {tipo_mov.lower()} superado. "
                       f"Límite: ${limite:,.2f} | Ya operado hoy: ${ya_operado:,.2f} | "
                       f"Disponible: ${max(0, limite - ya_operado):,.2f}.")
    return True, ""
