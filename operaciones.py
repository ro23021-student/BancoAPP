"""
operaciones.py — Lógica de negocio del banco.
Cada operación actualiza tanto los saldos operativos (clientes)
como el libro mayor contable, garantizando consistencia.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from tiempo import hoy as _hoy
import re
MIN_TRANSACCION = Decimal("1.00")
from sqlalchemy import func
from models import Cliente, CuotaPrestamo, Movimiento, Prestamo, ConfigBanco, DepositoPlazoFijo, TarjetaCredito
from contabilidad import money, f, registrar, saldo_cuenta, caja_real


# ─── Validadores de formato ──────────────────────────────────

def _validar_dui(dui):
    """DUI salvadoreño: 8 dígitos, guión, 1 dígito. Ej: 06190312-5"""
    if not dui: return True, ""
    if not re.fullmatch(r"\d{8}-\d", dui.strip()):
        return False, "DUI inválido. Formato requerido: 00000000-0"
    return True, ""

def _validar_nit(nit):
    """NIT salvadoreño. Ej: 0614-190190-001-5"""
    if not nit: return True, ""
    if not re.fullmatch(r"\d{4}-\d{6}-\d{3}-\d", nit.strip()):
        return False, "NIT inválido. Formato requerido: 0000-000000-000-0"
    return True, ""

def _validar_telefono(tel):
    """Teléfono El Salvador: empieza con 2, 6 o 7. Ej: 7777-1234"""
    if not tel: return True, ""
    if not re.fullmatch(r"[267]\d{3}-\d{4}", tel.strip()):
        return False, "Teléfono inválido. Formato requerido: 7777-1234 (inicia con 2, 6 o 7)"
    return True, ""

def _validar_fecha_nacimiento(fecha):
    """Fecha YYYY-MM-DD. Edad entre 15 y 120 años."""
    if not fecha: return True, ""
    try:
        dt = datetime.strptime(fecha.strip(), "%Y-%m-%d")
    except ValueError:
        return False, "Fecha inválida. Formato requerido: YYYY-MM-DD (ej: 1990-05-15)"
    edad = (datetime.now() - dt).days // 365
    if edad < 15:
        return False, f"El cliente debe tener al menos 15 años (edad calculada: {edad})"
    if edad > 120:
        return False, f"Fecha de nacimiento no válida (edad calculada: {edad} años)"
    return True, ""

def _validar_email(email):
    """Email formato usuario@dominio.ext"""
    if not email: return True, ""
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", email.strip()):
        return False, "Email inválido. Formato requerido: nombre@correo.com"
    return True, ""


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
        ok_e, msg_e = _validar_email(email)
        if not ok_e:
            return False, msg_e

    if documento and tipo_documento == "DUI":
        ok_d, msg_d = _validar_dui(documento)
        if not ok_d:
            return False, msg_d

    if nit:
        ok_n, msg_n = _validar_nit(nit)
        if not ok_n:
            return False, msg_n

    if telefono and telefono.strip():
        ok_t, msg_t = _validar_telefono(telefono.strip())
        if not ok_t:
            return False, msg_t

    if fecha_nacimiento and fecha_nacimiento.strip():
        ok_f, msg_f = _validar_fecha_nacimiento(fecha_nacimiento.strip())
        if not ok_f:
            return False, msg_f

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
        session.refresh(cliente)
        return True, cliente
    except Exception as e:
        session.rollback()
        return False, f"Error: {str(e)}"


#───────Extras─────────────────────────

def abrir_plazo_fijo(session, cliente_id, monto, tasa, meses):
    from datetime import date as _date
    monto_dec = money(monto)
    meses     = int(meses)

    # Validaciones
    if monto_dec <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    if meses <= 0:
        raise ValueError("El plazo en meses debe ser mayor a cero.")
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        raise ValueError(f"Cliente con ID {cliente_id} no encontrado.")

    # Cálculos financieros
    tasa_dec          = Decimal(str(tasa))
    interes_proyectado = money(monto_dec * tasa_dec * Decimal(meses) / Decimal("12"))
    monto_total        = money(monto_dec + interes_proyectado)
    hoy                = _hoy()
    fecha_venc         = _date(
        hoy.year + (hoy.month + meses - 1) // 12,
        (hoy.month + meses - 1) % 12 + 1,
        hoy.day,
    )

    try:
        # 1. Lógica Operativa: Crear el registro en models.py
        nuevo_dpf = DepositoPlazoFijo(
            cliente_id=cliente_id,
            monto=monto_dec,
            tasa_anual=tasa_dec,
            plazo_meses=meses,
            fecha_apertura=hoy,
            fecha_vencimiento=fecha_venc,
            interes_proyectado=interes_proyectado,
            monto_total=monto_total,
            estado="ACTIVO",
        )
        session.add(nuevo_dpf)
        session.flush()

        # 2. Lógica Contable: La partida doble
        registrar(
            session=session,
            debitos=[("Caja General", monto_dec)],              # Entra dinero al banco
            creditos=[("Depositos a Plazo Fijo", monto_dec)],   # Nace la deuda con el cliente
            descripcion=f"Apertura de Plazo Fijo para cliente ID: {cliente_id}"
        )

        session.commit()
        return nuevo_dpf
    except Exception:
        session.rollback()
        raise


def consumir_tarjeta_credito(session, tarjeta_id, monto_consumo):
    monto_dec = money(monto_consumo)

    # Validaciones
    if monto_dec <= 0:
        raise ValueError("El monto del consumo debe ser mayor a cero.")
    tarjeta = session.query(TarjetaCredito).filter_by(id=tarjeta_id).first()
    if not tarjeta:
        raise ValueError("Tarjeta no encontrada.")
    disponible = tarjeta.limite - tarjeta.saldo_usado
    if monto_dec > disponible:
        raise ValueError(
            f"Límite de crédito insuficiente. "
            f"Disponible: ${disponible:,.2f} | Solicitado: ${monto_dec:,.2f}"
        )

    try:
        # 1. Lógica Operativa: Aumentar la deuda en la tarjeta
        tarjeta.saldo_usado += monto_dec

        # 2. Lógica Contable: La partida doble
        registrar(
            session=session,
            debitos=[("Deudores por Tarjeta", monto_dec)], # Aumenta nuestro derecho a cobrar
            creditos=[("Caja General", monto_dec)],        # Sale el dinero del banco
            descripcion=f"Consumo TC terminada en {tarjeta.numero[-4:]}"
        )

        session.commit()
        return tarjeta
    except Exception:
        session.rollback()
        raise


def pagar_intereses_dpf(session, cliente_id, monto_interes):
    monto_dec = money(monto_interes)

    # Validaciones
    if monto_dec <= 0:
        raise ValueError("El monto de intereses debe ser mayor a cero.")
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        raise ValueError(f"Cliente con ID {cliente_id} no encontrado.")

    try:
        # 1. Lógica Operativa: Depositar el dinero en la cuenta de ahorros del cliente
        cliente.saldo += monto_dec

        # 2. Lógica Contable: Reconocer el gasto y aumentar el saldo del cliente
        registrar(
            session=session,
            debitos=[("Gastos Intereses", monto_dec)],      # El banco registra la pérdida/gasto
            creditos=[("Depositos Clientes", monto_dec)],   # Aumenta el saldo disponible del cliente
            descripcion=f"Pago de intereses de Plazo Fijo al cliente ID: {cliente_id}"
        )

        session.commit()
    except Exception:
        session.rollback()
        raise

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
    "nombre", "tipo", "tipo_cuenta_id", "documento", "tipo_documento",
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
            ok_e, msg_e = _validar_email(valor)
            if not ok_e:
                return False, msg_e
            valor = valor.lower()
        if campo == "documento" and valor and kwargs.get("tipo_documento", cliente.tipo_documento) == "DUI":
            ok_d, msg_d = _validar_dui(valor)
            if not ok_d:
                return False, msg_d
        if campo == "telefono" and valor:
            ok_t, msg_t = _validar_telefono(valor)
            if not ok_t:
                return False, msg_t
        if campo == "fecha_nacimiento" and valor:
            ok_f, msg_f = _validar_fecha_nacimiento(valor)
            if not ok_f:
                return False, msg_f
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
    _mov( session,cliente_id,"Deposito",money(bruto),f"Depósito (bruto ${money(bruto):.2f}, comisión ${money(comision):.2f})")
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
        return False, f"El monto mínimo de retiro es ${MIN_TRANSACCION}"
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
        return False, f"El monto mínimo de transferencia es ${MIN_TRANSACCION}"

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

def otorgar_prestamo(session, cliente_id, monto, plazo_meses, tasa_anual=None):
    """
    El banco otorga un préstamo al cliente.
    tasa_anual: fracción decimal (ej: 0.15 = 15%). Si no se pasa, usa la tasa del sistema.

    Asiento:
      Débito  Préstamos x Cobrar   monto   ← se crea el activo
      Crédito Depósitos Clientes   monto   ← queda en cuenta del cliente
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

    # Verificar capacidad patrimonial
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

    # Usar tasa enviada por el formulario o la del sistema
    if tasa_anual is not None:
        tasa_dec = Decimal(str(tasa_anual))
    else:
        tasa_dec = tasa_prestamo(session)

    interes = f(monto * tasa_dec)

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

    capital_por_cuota = f(monto / plazo_meses)
    interes_por_cuota = f(interes / plazo_meses)

    # La última cuota absorbe el centavo residual del redondeo
    # para que la suma de todas las cuotas sea exactamente igual al total
    capital_residuo = monto   - capital_por_cuota * plazo_meses
    interes_residuo = interes - interes_por_cuota * plazo_meses

    for n in range(1, plazo_meses + 1):
        es_ultima = (n == plazo_meses)

        cap  = money(capital_por_cuota + (capital_residuo if es_ultima else Decimal("0")))
        inte = money(interes_por_cuota + (interes_residuo if es_ultima else Decimal("0")))

        saldo = money(max(Decimal("0"), saldo - cap))

        cuota = CuotaPrestamo(
            prestamo_id=p.id,
            numero_cuota=n,
            fecha_vencimiento=
                datetime.utcnow().date()
                + timedelta(days=n*30),
            monto_cuota=cap + inte,
            capital=cap,
            interes=inte,
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
        session.refresh(p)
        return True, p
    except Exception as e:
        session.rollback()
        return False, f"Error al otorgar préstamo: {str(e)}"


def pagar_prestamo(session, cliente_id, monto_pago):
    """
    Paga cuotas del préstamo activo más antiguo, cuota por cuota.
    Cada cuota tiene su capital e interés ya definidos en la tabla CuotaPrestamo.
    El pago cubre cuotas completas en orden; el sobrante queda sin aplicar.

    Asientos por cuota pagada:
      Débito  Depositos Clientes   capital + interes
      Crédito Prestamos x Cobrar   capital
      Crédito Ingresos Intereses   interes
    """
    from datetime import datetime as _dt

    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no existe"

    monto_pago = f(monto_pago)
    if monto_pago <= 0:
        return False, "El monto debe ser mayor a cero"

    # Préstamo activo más antiguo con cuotas pendientes
    p = (session.query(Prestamo)
         .filter_by(cliente_id=cliente_id, estado="ACTIVO")
         .filter(Prestamo.saldo_pendiente > 0)
         .order_by(Prestamo.fecha)
         .first())
    if not p:
        return False, "No hay préstamo activo con saldo pendiente"

    if monto_pago > money(cliente.saldo):
        return False, f"Saldo insuficiente (tiene ${cliente.saldo:,.2f})"

    # Cuotas pendientes o vencidas en orden
    cuotas_pendientes = (
        session.query(CuotaPrestamo)
        .filter(CuotaPrestamo.prestamo_id == p.id,
                CuotaPrestamo.estado.in_(["PENDIENTE", "VENCIDA"]))
        .order_by(CuotaPrestamo.numero_cuota)
        .all()
    )
    if not cuotas_pendientes:
        return False, "No hay cuotas pendientes"

    # Verificar que el pago alcanza al menos una cuota completa
    # EXCEPCIÓN: si es la última cuota y el saldo_pendiente es menor a la cuota normal
    # (diferencias de centavos acumuladas), se permite pagar el residuo.
    primera_cuota   = cuotas_pendientes[0]
    monto_cuota     = money(primera_cuota.monto_cuota)
    es_ultima_cuota = len(cuotas_pendientes) == 1
    saldo_residual  = money(p.saldo_pendiente)

    if es_ultima_cuota and saldo_residual < monto_cuota:
        # La cuota tiene centavos residuales: aceptar el pago exacto del saldo pendiente
        monto_cuota = saldo_residual

    if monto_pago < monto_cuota - Decimal("0.02"):
        return False, (f"El monto mínimo para pagar es ${monto_cuota:,.2f} "
                       f"(cuota #{primera_cuota.numero_cuota})")

    # Aplicar pago cuota por cuota
    restante         = monto_pago
    cuotas_pagadas   = 0
    total_capital    = Decimal("0")
    total_interes    = Decimal("0")
    total_cuotas     = len(cuotas_pendientes)

    for idx, cuota in enumerate(cuotas_pendientes):
        cuota_capital = money(cuota.capital)
        cuota_interes = money(cuota.interes)
        cuota_monto   = money(cuota.monto_cuota)

        # Si el pago restante cubre todo lo que falta (última cuota o pago total),
        # aceptar aunque haya diferencia de centavos por redondeo
        es_ultima = (idx == total_cuotas - 1)
        tolerancia = Decimal("0.10") if es_ultima else Decimal("0.02")

        if restante < cuota_monto - tolerancia:
            break  # no alcanza para esta cuota

        # Marcar cuota como PAGADA
        cuota.estado     = "PAGADA"
        cuota.fecha_pago = _dt.now()

        total_capital += cuota_capital
        total_interes += cuota_interes
        restante      -= cuota_monto
        cuotas_pagadas += 1

    if cuotas_pagadas == 0:
        return False, f"Monto insuficiente para cubrir la cuota de ${monto_cuota:,.2f}"

    monto_aplicado = money(total_capital + total_interes)

    # ─ Asiento capital ─
    if total_capital > Decimal("0.005"):
        registrar(
            session,
            debitos=[("Depositos Clientes", total_capital)],
            creditos=[("Prestamos x Cobrar", total_capital)],
            descripcion=f"Pago capital préstamo ID {p.id} — {cuotas_pagadas} cuota(s)",
        )

    # ─ Asiento intereses ─
    if total_interes > Decimal("0.005"):
        registrar(
            session,
            debitos=[("Depositos Clientes", total_interes)],
            creditos=[("Ingresos Intereses", total_interes)],
            descripcion=f"Pago interés préstamo ID {p.id} — {cuotas_pagadas} cuota(s)",
        )

    # Actualizar saldo del préstamo
    # Si se pagaron TODAS las cuotas pendientes, forzar saldo a 0 exacto
    # (evita centavos residuales de redondeo que dejan el préstamo "casi pagado")
    todas_pagadas = (cuotas_pagadas == total_cuotas)
    if todas_pagadas:
        nuevo_saldo = Decimal("0")
    else:
        nuevo_saldo = money(max(Decimal("0"), p.saldo_pendiente - total_capital))
    p.saldo_pendiente = nuevo_saldo
    p.interes_pagado  = money(p.interes_pagado + total_interes)
    if nuevo_saldo < Decimal("0.005") or todas_pagadas:
        p.estado = "PAGADO"

    session.flush()

    # Descontar del saldo del cliente solo lo que se aplicó
    _set_saldo(session, cliente_id, money(cliente.saldo) - monto_aplicado)
    _mov(session, cliente_id, "Pago Prestamo", monto_aplicado,
         f"Pago {cuotas_pagadas} cuota(s) — capital ${total_capital:.2f} + interés ${total_interes:.2f}")

    try:
        session.commit()
        return True, (f"✅ {cuotas_pagadas} cuota(s) pagada(s) — "
                      f"capital ${total_capital:.2f} + interés ${total_interes:.2f} "
                      f"= ${monto_aplicado:.2f}")
    except Exception as e:
        session.rollback()
        return False, f"Error al pagar préstamo: {str(e)}"


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

    # Verificar primero si ya fue revertido
    ya_revertido = session.query(Movimiento).filter(
        Movimiento.tipo == "Reverso",
        Movimiento.descripcion.like(f"REVERSO mov#{movimiento_id}%"),
        Movimiento.cliente_id == mov.cliente_id,
    ).first()

    if ya_revertido:
        return False, "Esta operación ya fue revertida anteriormente."

    limite = datetime.utcnow() - timedelta(minutes=VENTANA_REVERSION_MIN)

    if mov.fecha < limite:
        return False, (f"Solo se pueden revertir operaciones de los últimos "
                       f"{VENTANA_REVERSION_MIN} minutos.")

    cliente = _get_cliente(session, mov.cliente_id)
    if not cliente:
        return False, "Cliente no encontrado"

    monto = money(mov.monto)

    try:
        if mov.tipo == "Deposito":
            # El depósito original acreditó el neto (bruto - comisión) al cliente.
            # La reversión debe quitar solo el neto acreditado, no el bruto.
            comision_dep = money(monto * tasa_deposito(session))
            neto_dep     = money(monto - comision_dep)
            if neto_dep > cliente.saldo:
                return False, (f"Saldo insuficiente para revertir "
                               f"(cliente tiene ${cliente.saldo:,.2f}, "
                               f"reversión requiere ${neto_dep:,.2f}).")
            registrar(
                session,
                debitos=[(  "Depositos Clientes", neto_dep)],
                creditos=[( "Caja General",        neto_dep)],
                descripcion=f"Reverso depósito mov#{movimiento_id}",
            )
            _set_saldo(session, cliente.id, cliente.saldo - neto_dep)

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
        print("ERROR REVERSION:", e)
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


def procesar_apertura_plazo_fijo(session, cliente_id: int, monto: float, tasa: float, meses: int):
    """Valida y registra un plazo fijo financiado en efectivo (Caja)."""
    from datetime import date as _date
    monto_dec = money(monto)
    meses     = int(meses)

    if monto_dec <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    if meses <= 0:
        raise ValueError("El plazo en meses debe ser mayor a cero.")

    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        raise ValueError("Cliente no encontrado.")

    # Cálculos financieros
    tasa_dec           = Decimal(str(tasa))
    interes_proyectado = money(monto_dec * tasa_dec * Decimal(meses) / Decimal("12"))
    monto_total        = money(monto_dec + interes_proyectado)
    hoy                = _hoy()
    fecha_venc         = _date(
        hoy.year + (hoy.month + meses - 1) // 12,
        (hoy.month + meses - 1) % 12 + 1,
        hoy.day,
    )

    # 1. Registro Operativo
    nuevo_dpf = DepositoPlazoFijo(
        cliente_id=cliente_id,
        monto=monto_dec,
        tasa_anual=tasa_dec,
        plazo_meses=meses,
        fecha_apertura=hoy,
        fecha_vencimiento=fecha_venc,
        interes_proyectado=interes_proyectado,
        monto_total=monto_total,
        estado="ACTIVO",
    )
    session.add(nuevo_dpf)
    session.flush()

    # 2. Registro Contable Automatizado
    registrar(
        session=session,
        debitos=[("Caja General", monto_dec)],              # Entra efectivo al banco
        creditos=[("Depositos a Plazo Fijo", monto_dec)],   # Nueva obligación financiera
        descripcion=f"Apertura DPF #{nuevo_dpf.id} - Cliente ID: {cliente_id}"
    )
    return nuevo_dpf

def procesar_consumo_tarjeta(session, tarjeta_id: int, monto_consumo: float):
    """Valida el límite disponible y procesa un consumo de tarjeta de crédito."""
    monto_dec = money(monto_consumo)
    if monto_dec <= 0:
        raise ValueError("El monto del consumo debe ser mayor a cero.")

    tarjeta = session.query(TarjetaCredito).filter_by(id=tarjeta_id).first()
    if not tarjeta:
        raise ValueError("Tarjeta de crédito no encontrada o inválida.")
        
    # Validación de regla de negocio
    disponible = tarjeta.limite - tarjeta.saldo_usado
    if monto_dec > disponible:
        raise ValueError(f"Fondos insuficientes. Disponible: ${disponible:,.2f}")

    # 1. Modificación del estado operativo
    tarjeta.saldo_usado += monto_dec

    # 2. Asiento contable de partida doble
    registrar(
        session=session,
        debitos=[("Deudores por Tarjeta", monto_dec)], # Derecho de cobro (Activo)
        creditos=[("Caja General", monto_dec)],        # Liquidación de fondos de salida (Activo)
        descripcion=f"Consumo TC #{tarjeta.id} - Ref: Autorización automática"
    )
    return tarjeta

# ═══════════════════════════════════════════════════════════════════
# NUEVAS OPERACIONES — CUENTAS AMPLIADAS DEL PLAN CONTABLE
# ═══════════════════════════════════════════════════════════════════

# ── INVERSIONES ────────────────────────────────────────────────────

def registrar_inversion(session, monto: float, descripcion: str = "Inversión en valores"):
    """
    El banco invierte dinero (bonos, valores, títulos).
      Débito  Inversiones       monto   (nace un activo)
      Crédito Caja General      monto   (sale el dinero)
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Inversiones", monto_dec)],
              creditos=[("Caja General", monto_dec)],
              descripcion=descripcion)
    session.flush()
    return True, f"Inversión de ${monto_dec:,.2f} registrada"


def liquidar_inversion(session, monto: float, ganancia: float = 0.0, descripcion: str = "Liquidación de inversión"):
    """
    El banco recupera una inversión (con o sin ganancia).
      Débito  Caja General         monto + ganancia
      Crédito Inversiones          monto
      Crédito Ingresos Intereses   ganancia  (si la hay)
    """
    monto_dec   = money(monto)
    ganancia_dec = money(ganancia)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    debitos  = [("Caja General", monto_dec + ganancia_dec)]
    creditos = [("Inversiones", monto_dec)]
    if ganancia_dec > 0:
        creditos.append(("Ingresos Intereses", ganancia_dec))
    registrar(session, debitos=debitos, creditos=creditos, descripcion=descripcion)
    session.flush()
    return True, f"Inversión liquidada. Recuperado: ${monto_dec + ganancia_dec:,.2f}"


# ── BIENES E INMUEBLES ─────────────────────────────────────────────

def adquirir_bien_inmueble(session, monto: float, descripcion: str = "Adquisición de bien"):
    """
    El banco compra un edificio, equipo o mobiliario.
      Débito  Bienes e Inmuebles   monto
      Crédito Caja General         monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Bienes e Inmuebles", monto_dec)],
              creditos=[("Caja General", monto_dec)],
              descripcion=descripcion)
    session.flush()
    return True, f"Bien/inmueble registrado por ${monto_dec:,.2f}"


# ── PRÉSTAMOS MOROSOS Y PROVISIÓN ──────────────────────────────────

def clasificar_prestamo_moroso(session, prestamo_id: int):
    """
    Mueve un préstamo de 'Prestamos x Cobrar' a 'Prestamos Morosos'
    cuando entra en mora. Asiento espejo:
      Débito  Prestamos Morosos     saldo_pendiente
      Crédito Prestamos x Cobrar    saldo_pendiente
    """
    from models import Prestamo as PrestamoModel
    p = session.query(PrestamoModel).filter_by(id=prestamo_id).first()
    if not p:
        return False, "Préstamo no encontrado"
    if p.estado != "ACTIVO":
        return False, "Solo se pueden clasificar préstamos activos"
    saldo = money(p.saldo_pendiente)
    if saldo <= 0:
        return False, "Préstamo ya cancelado"
    registrar(session,
              debitos=[("Prestamos Morosos", saldo)],
              creditos=[("Prestamos x Cobrar", saldo)],
              descripcion=f"Clasificación moroso — préstamo #{prestamo_id}")
    p.estado = "MOROSO"
    session.flush()
    return True, f"Préstamo #{prestamo_id} clasificado como moroso (${saldo:,.2f})"


def constituir_provision(session, monto: float, descripcion: str = "Provisión para préstamos incobrables"):
    """
    Registra provisión para préstamos que podrían no cobrarse.
    La cuenta 'Provision Incobrables' es contra-activo (tipo_normal=C),
    por lo tanto un CRÉDITO la incrementa (reduce el valor neto de activos).
      Débito  Gastos por Provisiones    monto
      Crédito Provision Incobrables     monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Gastos por Provisiones", monto_dec)],
              creditos=[("Provision Incobrables", monto_dec)],
              descripcion=descripcion)
    session.flush()
    return True, f"Provisión constituida por ${monto_dec:,.2f}"


def castigar_prestamo_incobrable(session, prestamo_id: int):
    """
    Castiga (da de baja) un préstamo definitivamente incobrable.
    Usa la provisión acumulada contra el saldo moroso.
      Débito  Provision Incobrables    saldo
      Crédito Prestamos Morosos        saldo
    Si la provisión es insuficiente, el exceso va a Gastos por Provisiones.
    """
    from models import Prestamo as PrestamoModel
    p = session.query(PrestamoModel).filter_by(id=prestamo_id).first()
    if not p:
        return False, "Préstamo no encontrado"
    saldo = money(p.saldo_pendiente)
    if saldo <= 0:
        return False, "Sin saldo pendiente"

    # Usar la provisión disponible; exceso va a gastos directo
    prov_disp = money(saldo_cuenta(session, "Provision Incobrables"))
    if prov_disp >= saldo:
        registrar(session,
                  debitos=[("Provision Incobrables", saldo)],
                  creditos=[("Prestamos Morosos", saldo)],
                  descripcion=f"Castigo préstamo incobrable #{prestamo_id}")
    else:
        exceso = saldo - prov_disp
        debitos = []
        if prov_disp > 0:
            debitos.append(("Provision Incobrables", prov_disp))
        debitos.append(("Gastos por Provisiones", exceso))
        registrar(session,
                  debitos=debitos,
                  creditos=[("Prestamos Morosos", saldo)],
                  descripcion=f"Castigo préstamo incobrable #{prestamo_id} (exceso a gastos)")
    p.estado = "CASTIGADO"
    p.saldo_pendiente = money(0)
    session.flush()
    return True, f"Préstamo #{prestamo_id} castigado por ${saldo:,.2f}"


# ── CUENTAS DE AHORRO / CORRIENTES (separadas) ─────────────────────

def deposito_cuenta_ahorro(session, cliente_id: int, monto: float):
    """
    Depósito en cuenta de ahorro (pasivo separado de Depositos Clientes).
      Débito  Caja General       monto
      Crédito Cuentas de Ahorro  monto
    También actualiza el saldo operativo del cliente.
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no encontrado"
    _set_saldo(session, cliente_id, money(cliente.saldo) + monto_dec)
    registrar(session,
              debitos=[("Caja General", monto_dec)],
              creditos=[("Cuentas de Ahorro", monto_dec)],
              descripcion=f"Depósito ahorro cliente #{cliente_id}")
    _mov(session, cliente_id, "Deposito Ahorro", monto_dec, f"Depósito cuenta ahorro ${monto_dec:,.2f}")
    session.flush()
    return True, f"Depósito de ahorro de ${monto_dec:,.2f} registrado"


def deposito_cuenta_corriente(session, cliente_id: int, monto: float):
    """
    Depósito en cuenta corriente.
      Débito  Caja General          monto
      Crédito Cuentas Corrientes    monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no encontrado"
    _set_saldo(session, cliente_id, money(cliente.saldo) + monto_dec)
    registrar(session,
              debitos=[("Caja General", monto_dec)],
              creditos=[("Cuentas Corrientes", monto_dec)],
              descripcion=f"Depósito corriente cliente #{cliente_id}")
    _mov(session, cliente_id, "Deposito Corriente", monto_dec, f"Depósito cuenta corriente ${monto_dec:,.2f}")
    session.flush()
    return True, f"Depósito corriente de ${monto_dec:,.2f} registrado"


# ── OBLIGACIONES CON BANCOS ────────────────────────────────────────

def recibir_prestamo_banco(session, monto: float, banco_nombre: str = "Banco Externo"):
    """
    El banco recibe un préstamo de otro banco (fondeo interbancario).
      Débito  Caja General              monto
      Crédito Obligaciones con Bancos   monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Caja General", monto_dec)],
              creditos=[("Obligaciones con Bancos", monto_dec)],
              descripcion=f"Préstamo interbancario recibido de {banco_nombre}")
    session.flush()
    return True, f"Obligación con {banco_nombre} registrada por ${monto_dec:,.2f}"


def pagar_obligacion_banco(session, monto: float, interes: float = 0.0, banco_nombre: str = "Banco Externo"):
    """
    El banco paga cuota a otro banco (capital + interés).
      Débito  Obligaciones con Bancos   capital
      Débito  Gastos Intereses          interes
      Crédito Caja General              capital + interes
    """
    capital_dec  = money(monto)
    interes_dec  = money(interes)
    total        = capital_dec + interes_dec
    if capital_dec <= 0:
        return False, "El monto de capital debe ser positivo"
    debitos = [("Obligaciones con Bancos", capital_dec)]
    if interes_dec > 0:
        debitos.append(("Gastos Intereses", interes_dec))
    registrar(session,
              debitos=debitos,
              creditos=[("Caja General", total)],
              descripcion=f"Pago obligación interbancaria a {banco_nombre}")
    session.flush()
    return True, f"Pago interbancario registrado: capital ${capital_dec:,.2f} + interés ${interes_dec:,.2f}"


# ── IMPUESTOS POR PAGAR ────────────────────────────────────────────

def provisionar_impuesto(session, monto: float, tipo_impuesto: str = "Renta"):
    """
    El banco registra un impuesto devengado pero no pagado aún.
      Débito  Gastos Operativos     monto
      Crédito Impuestos por Pagar   monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Gastos Operativos", monto_dec)],
              creditos=[("Impuestos por Pagar", monto_dec)],
              descripcion=f"Provisión impuesto {tipo_impuesto}")
    session.flush()
    return True, f"Impuesto {tipo_impuesto} provisionado por ${monto_dec:,.2f}"


def pagar_impuesto(session, monto: float, tipo_impuesto: str = "Renta"):
    """
    El banco paga un impuesto ya provisionado.
      Débito  Impuestos por Pagar   monto
      Crédito Caja General          monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Impuestos por Pagar", monto_dec)],
              creditos=[("Caja General", monto_dec)],
              descripcion=f"Pago impuesto {tipo_impuesto}")
    session.flush()
    return True, f"Impuesto {tipo_impuesto} pagado por ${monto_dec:,.2f}"


# ── RESERVAS LEGALES ───────────────────────────────────────────────

def constituir_reserva_legal(session, monto: float):
    """
    Reserva obligatoria por ley (BCR El Salvador).
    Proviene de las utilidades generadas.
      Débito  Utilidades del Ejercicio   monto
      Crédito Reservas Legales           monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Utilidades del Ejercicio", monto_dec)],
              creditos=[("Reservas Legales", monto_dec)],
              descripcion="Constitución de reserva legal — obligatoria BCR")
    session.flush()
    return True, f"Reserva legal constituida por ${monto_dec:,.2f}"


def registrar_utilidad_ejercicio(session, monto: float):
    """
    Cierre contable: traslada utilidades del período a Utilidades del Ejercicio.
    Típicamente se usa al cerrar el año fiscal.
      Débito  Ingresos Intereses       (parte)
      Débito  Ingresos Comisiones      (parte)
      Crédito Utilidades del Ejercicio  monto neto
    Para uso simple: registra directamente la utilidad neta.
      Débito  Capital Banco                monto  (origen: capital aportado)
      Crédito Utilidades del Ejercicio     monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Capital Banco", monto_dec)],
              creditos=[("Utilidades del Ejercicio", monto_dec)],
              descripcion="Registro utilidades del ejercicio — cierre período")
    session.flush()
    return True, f"Utilidades del ejercicio registradas por ${monto_dec:,.2f}"


# ── INGRESOS POR TARJETA DE CRÉDITO (comisiones al comercio) ───────

def registrar_comision_tarjeta_credito(session, monto: float, descripcion: str = "Comisión TC comercio"):
    """
    Comisiones que cobra el banco al comercio cuando un cliente usa su TC.
      Débito  Caja General               monto
      Crédito Ingresos Tarjeta Credito   monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Caja General", monto_dec)],
              creditos=[("Ingresos Tarjeta Credito", monto_dec)],
              descripcion=descripcion)
    session.flush()
    return True, f"Comisión TC registrada: ${monto_dec:,.2f}"


# ── INGRESOS POR MORA (intereses de penalización) ──────────────────

def cobrar_mora_prestamo(session, cliente_id: int, monto: float):
    """
    Cobra intereses de mora por pagos tardíos al cliente.
    Se descuenta del saldo del cliente y se registra como ingreso.
      Débito  Depositos Clientes   monto
      Crédito Ingresos por Mora    monto

    Distribuye el cobro entre TODOS los préstamos con mora del cliente
    (ordenados por mora descendente), no solo el de mayor mora.
    Valida que el monto no exceda la mora total acumulada real.
    Resetea dias_mora a 0 cuando mora_acumulada llega a 0.
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"

    cliente = _get_cliente(session, cliente_id)
    if not cliente:
        return False, "Cliente no encontrado"

    # Obtener TODOS los préstamos con mora del cliente (ACTIVO y MOROSO)
    prestamos_con_mora = (session.query(Prestamo)
                          .filter(Prestamo.cliente_id == cliente_id)
                          .filter(Prestamo.estado.in_(["ACTIVO", "MOROSO"]))
                          .filter(Prestamo.mora_acumulada > 0)
                          .order_by(Prestamo.mora_acumulada.desc())
                          .all())

    # FIX Bug 4: validar que monto no exceda mora total real
    mora_total = sum(money(p.mora_acumulada) for p in prestamos_con_mora)
    if mora_total == 0:
        return False, "Este cliente no tiene mora acumulada en ningún préstamo"
    if monto_dec > mora_total:
        return False, (f"El monto ${monto_dec:,.2f} excede la mora total acumulada "
                       f"${mora_total:,.2f}. Ingrese un monto menor o igual.")

    # FIX Bug 1: validar saldo del cliente
    if money(cliente.saldo) < monto_dec:
        return False, f"Saldo insuficiente para cobrar mora (saldo: ${cliente.saldo:,.2f})"

    # Descontar del saldo del cliente y registrar contablemente
    _set_saldo(session, cliente_id, money(cliente.saldo) - monto_dec)
    registrar(session,
              debitos=[("Depositos Clientes", monto_dec)],
              creditos=[("Ingresos por Mora", monto_dec)],
              descripcion=f"Cobro mora cliente #{cliente_id}")
    _mov(session, cliente_id, "Mora Cobrada", monto_dec,
         f"Intereses de mora ${monto_dec:,.2f} ({len(prestamos_con_mora)} préstamo(s))")

    # FIX Bug 1: distribuir el cobro entre TODOS los préstamos con mora,
    # de mayor a menor, hasta agotar el monto cobrado.
    restante = monto_dec
    for p in prestamos_con_mora:
        if restante <= 0:
            break
        mora_p = money(p.mora_acumulada)
        aplicar = min(mora_p, restante)
        p.mora_acumulada = mora_p - aplicar
        restante -= aplicar
        # FIX Bug 2: resetear dias_mora cuando mora llega a 0
        if p.mora_acumulada == Decimal("0"):
            p.dias_mora = 0

    session.flush()
    return True, f"Mora de ${monto_dec:,.2f} cobrada al cliente #{cliente_id} (mora total era ${mora_total:,.2f})"


# ── GASTOS OPERATIVOS (salarios, alquiler, servicios) ──────────────

def registrar_gasto_operativo(session, monto: float, descripcion: str = "Gasto operativo"):
    """
    Registra gastos del banco: salarios, alquiler, servicios, etc.
      Débito  Gastos Operativos   monto
      Crédito Caja General        monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Gastos Operativos", monto_dec)],
              creditos=[("Caja General", monto_dec)],
              descripcion=descripcion)
    session.flush()
    return True, f"Gasto operativo registrado: ${monto_dec:,.2f}"


# ── GASTOS POR MORA PAGADA (multas que paga el banco) ──────────────

def registrar_mora_pagada_banco(session, monto: float, descripcion: str = "Mora pagada por el banco"):
    """
    Multa o mora que el banco debe pagar (ej: a reguladores, otros bancos).
      Débito  Gastos por Mora Pagada   monto
      Crédito Caja General             monto
    """
    monto_dec = money(monto)
    if monto_dec <= 0:
        return False, "El monto debe ser positivo"
    registrar(session,
              debitos=[("Gastos por Mora Pagada", monto_dec)],
              creditos=[("Caja General", monto_dec)],
              descripcion=descripcion)
    session.flush()
    return True, f"Mora pagada por el banco: ${monto_dec:,.2f}"