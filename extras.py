"""
extras.py — Lógica de negocio para funciones bancarias avanzadas.
Incluye: TipoCuenta, Beneficiarios, Plazo Fijo, Garantías, Score crediticio,
Tarjetas, Sucursales, ATM, AML, KYC, Cierre Diario, Socios/Aportes,
Refinanciamiento, Balance General, Estado de Resultados.
"""

from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
import random, string
from tiempo import hoy as _hoy
from sqlalchemy import func, text
from contabilidad import money, registrar, saldo_cuenta
from models import (
    Cliente, Movimiento, Prestamo, CuotaPrestamo,
    TipoCuenta, Beneficiario, DepositoPlazoFijo, Garantia,
    ScoreCredito, TarjetaDebito, TarjetaCredito,
    Sucursal, ATM, AlertaAML, CierreDiario,
    Socio, AporteSocio, CuentaContable, LineaAsiento, ConfigBanco,
)


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _cfg(session, clave, default="0"):
    r = session.query(ConfigBanco).filter_by(clave=clave).first()
    return r.valor if r else default


# ─────────────────────────────────────────────────────────────
# TIPOS DE CUENTA
# ─────────────────────────────────────────────────────────────

TIPOS_CUENTA_DEFAULT = [
    {"nombre": "Ahorro",       "tasa_interes": 0.0300, "saldo_minimo": 25.00,   "cobra_comision": False, "descripcion": "Cuenta de ahorro estándar"},
    {"nombre": "Corriente",    "tasa_interes": 0.0000, "saldo_minimo": 100.00,  "cobra_comision": True,  "descripcion": "Cuenta corriente para empresas y personas"},
    {"nombre": "Infantil",     "tasa_interes": 0.0400, "saldo_minimo": 0.00,    "cobra_comision": False, "descripcion": "Cuenta para menores de 18 años"},
    {"nombre": "Empresarial",  "tasa_interes": 0.0150, "saldo_minimo": 500.00,  "cobra_comision": True,  "descripcion": "Cuenta para personas jurídicas"},
    {"nombre": "Plazo Fijo",   "tasa_interes": 0.0500, "saldo_minimo": 500.00,  "cobra_comision": False, "descripcion": "Certificado de depósito a plazo"},
]

def inicializar_tipos_cuenta(session):
    """Crea los tipos de cuenta por defecto si no existen."""
    for t in TIPOS_CUENTA_DEFAULT:
        if not session.query(TipoCuenta).filter_by(nombre=t["nombre"]).first():
            session.add(TipoCuenta(**t))
    session.flush()


def crear_tipo_cuenta(session, nombre, tasa, saldo_minimo, cobra_comision, descripcion=""):
    if session.query(TipoCuenta).filter_by(nombre=nombre).first():
        return False, "Ya existe un tipo de cuenta con ese nombre"
    tc = TipoCuenta(
        nombre=nombre,
        tasa_interes=money(tasa),
        saldo_minimo=money(saldo_minimo),
        cobra_comision=cobra_comision,
        descripcion=descripcion,
    )
    session.add(tc)
    session.flush()
    return True, tc


# ─────────────────────────────────────────────────────────────
# SUCURSALES
# ─────────────────────────────────────────────────────────────

SUCURSALES_DEFAULT = [
    {"nombre": "Central",    "direccion": "1a Calle Poniente, San Salvador", "telefono": "2222-0000"},
    {"nombre": "Apopa",      "direccion": "Col. San José, Apopa",            "telefono": "2232-0001"},
    {"nombre": "Santa Ana",  "direccion": "4a Av. Sur, Santa Ana",           "telefono": "2447-0002"},
    {"nombre": "San Miguel", "direccion": "Av. Roosevelt, San Miguel",        "telefono": "2669-0003"},
]

def inicializar_sucursales(session):
    for s in SUCURSALES_DEFAULT:
        if not session.query(Sucursal).filter_by(nombre=s["nombre"]).first():
            session.add(Sucursal(**s))
    session.flush()

    # ATM por sucursal
    for suc in session.query(Sucursal).all():
        if not session.query(ATM).filter_by(sucursal_id=suc.id).first():
            session.add(ATM(
                sucursal_id=suc.id,
                ubicacion=f"ATM {suc.nombre} - Entrada principal",
                saldo_atm=money(10000),
            ))
    session.flush()


def crear_sucursal(session, nombre, direccion, telefono=""):

    if not nombre.strip():
        return False, "Debe ingresar el nombre"

    if not direccion.strip():
        return False, "Debe ingresar la dirección"

    if not telefono.strip():
        return False, "Debe ingresar el teléfono"

    if session.query(Sucursal).filter_by(nombre=nombre).first():
        return False, "Ya existe una sucursal con ese nombre"


# ─────────────────────────────────────────────────────────────
# BENEFICIARIOS FRECUENTES
# ─────────────────────────────────────────────────────────────

def agregar_beneficiario(session, cliente_id, cuenta_destino, alias):
    # Verificar que la cuenta destino exista
    destino = session.query(Cliente).filter_by(num_cuenta=cuenta_destino).first()
    if not destino:
        return False, "Número de cuenta destino no encontrado"
    if destino.id == cliente_id:
        return False, "No puedes agregarte a ti mismo como beneficiario"

    dup = session.query(Beneficiario).filter_by(
        cliente_id=cliente_id, cuenta_destino=cuenta_destino
    ).first()
    if dup:
        return False, "Este beneficiario ya está registrado"

    b = Beneficiario(
        cliente_id=cliente_id,
        cuenta_destino=cuenta_destino,
        nombre_destino=destino.nombre,
        alias=alias or destino.nombre,
    )
    session.add(b)
    session.flush()
    return True, b


def eliminar_beneficiario(session, beneficiario_id, cliente_id):
    b = session.query(Beneficiario).filter_by(id=beneficiario_id, cliente_id=cliente_id).first()
    if not b:
        return False, "Beneficiario no encontrado"
    session.delete(b)
    session.flush()
    return True, "Beneficiario eliminado"


# ─────────────────────────────────────────────────────────────
# DEPÓSITO A PLAZO FIJO
# ─────────────────────────────────────────────────────────────

def crear_deposito_plazo(session, cliente_id, monto, tasa_anual, plazo_meses, renovacion=False):
    
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        return False, "Cliente no encontrado"
    monto = money(monto)
    plazo_meses = int(plazo_meses)
    if monto < money(500):
        return False, "El monto mínimo para un plazo fijo es $500.00"
    if plazo_meses <= 0:
        return False, "El plazo debe ser mayor a cero"
    if cliente.saldo < monto:
        return False, "Saldo insuficiente"

    tasa = Decimal(str(tasa_anual))
    interes = money(monto * tasa * Decimal(plazo_meses) / Decimal("12"))
    from tiempo import hoy as _hoy
    monto_total = money(monto + interes)
    hoy = _hoy()
    fecha_venc = date(hoy.year + (hoy.month + plazo_meses - 1) // 12,
                      (hoy.month + plazo_meses - 1) % 12 + 1, hoy.day)

    # Débitar saldo
    cliente.saldo -= monto

    # Movimiento
    from models import _gen_num_trx
    session.add(Movimiento(
        num_trx=_gen_num_trx(),
        cliente_id=cliente_id,
        tipo="Plazo Fijo",
        monto=monto,
        descripcion=f"Apertura plazo fijo {plazo_meses} meses al {float(tasa)*100:.1f}%",
    ))

    dpf = DepositoPlazoFijo(
        cliente_id=cliente_id,
        monto=monto,
        tasa_anual=tasa,
        plazo_meses=plazo_meses,
        fecha_apertura=hoy,
        fecha_vencimiento=fecha_venc,
        interes_proyectado=interes,
        monto_total=monto_total,
        renovacion_automatica=renovacion,
    )
    session.add(dpf)

    # Asiento contable: el cliente retira de su cuenta vista (Depositos Clientes disminuye)
    # y el banco retiene el efectivo en Caja. Balance permanece cuadrado.
    registrar(session,
              debitos=[("Depositos Clientes", monto)],
              creditos=[("Caja General", monto)],
              descripcion=f"Plazo fijo cliente {cliente.nombre} - {plazo_meses} meses")
    session.flush()
    return True, dpf


def vencer_deposito_plazo(session, dpf_id, usuario_nombre="Sistema"):
    dpf = session.query(DepositoPlazoFijo).filter_by(id=dpf_id).first()
    if not dpf:
        return False, "Depósito no encontrado"
    if dpf.estado != "ACTIVO":
        return False, "El depósito no está activo"

    cliente = session.query(Cliente).filter_by(id=dpf.cliente_id).first()
    cliente.saldo += dpf.monto_total
    dpf.estado = "VENCIDO"

    from models import _gen_num_trx
    session.add(Movimiento(
        num_trx=_gen_num_trx(),
        cliente_id=dpf.cliente_id,
        tipo="Deposito",
        monto=dpf.monto_total,
        descripcion=f"Vencimiento plazo fijo #{dpf.num_certificado} (capital + intereses)",
    ))

    # Asiento de vencimiento: el banco devuelve al cliente capital + intereses.
    # El saldo del cliente sube en monto_total => Depositos Clientes sube igual.
    # Los intereses del DPF se acreditan a Ingresos Intereses por separado.
    registrar(session,
              debitos=[("Ingresos Intereses", dpf.interes_proyectado),
                       ("Caja General",       dpf.monto)],
              creditos=[("Depositos Clientes", dpf.monto_total)],
              descripcion=f"Vencimiento DPF {dpf.num_certificado}")
    session.flush()
    return True, dpf


# ─────────────────────────────────────────────────────────────
# GARANTÍAS
# ─────────────────────────────────────────────────────────────

def agregar_garantia(session, prestamo_id, tipo, descripcion, valor, num_registro=""):
    p = session.query(Prestamo).filter_by(id=prestamo_id).first()
    if not p:
        return False, "Préstamo no encontrado"
    g = Garantia(
        prestamo_id=prestamo_id,
        tipo=tipo,
        descripcion=descripcion,
        valor_estimado=money(valor),
        numero_registro=num_registro,
    )
    session.add(g)
    session.flush()
    return True, g


# ─────────────────────────────────────────────────────────────
# REFINANCIAMIENTO DE PRÉSTAMOS
# ─────────────────────────────────────────────────────────────

def refinanciar_prestamo(session, prestamo_id, nuevo_plazo_meses, tasa_anual=None):
    prestamo = session.query(Prestamo).filter_by(id=prestamo_id, estado="ACTIVO").first()
    if not prestamo:
        return False, "Préstamo no encontrado o no está activo"
    if float(prestamo.saldo_pendiente) <= 0:
        return False, "El préstamo ya está saldado"

    saldo = prestamo.saldo_pendiente
    if tasa_anual is None:
        tasa_anual = float(prestamo.interes) / float(prestamo.monto) * 12 / (prestamo.plazo_meses or 12)

    tasa_mensual = Decimal(str(tasa_anual)) / Decimal("12")
    interes_total = money(saldo * tasa_mensual * nuevo_plazo_meses)
    total = money(saldo + interes_total)
    cuota = money(total / nuevo_plazo_meses)

    hoy = _hoy()
    fecha_venc = date(hoy.year + (hoy.month + nuevo_plazo_meses - 1) // 12,
                      (hoy.month + nuevo_plazo_meses - 1) % 12 + 1, hoy.day)

    # Marcar viejo como refinanciado
    prestamo.estado = "REFINANCIADO"

    # Crear nuevo préstamo
    nuevo = Prestamo(
        cliente_id=prestamo.cliente_id,
        monto=saldo,
        interes=interes_total,
        saldo_pendiente=total,
        plazo_meses=nuevo_plazo_meses,
        cuota_mensual=cuota,
        fecha_vencimiento=fecha_venc,
        clasificacion=prestamo.clasificacion,
        prestamo_origen_id=prestamo.id,
        sucursal_id=prestamo.sucursal_id,
    )
    session.add(nuevo)
    session.flush()

    # Generar tabla de amortización
    saldo_rest = total
    for i in range(1, nuevo_plazo_meses + 1):
        interes_cuota = money(saldo_rest * tasa_mensual)
        capital_cuota = money(cuota - interes_cuota)
        saldo_rest = money(saldo_rest - capital_cuota)
        fv = date(hoy.year + (hoy.month + i - 1) // 12,
                  (hoy.month + i - 1) % 12 + 1, hoy.day)
        session.add(CuotaPrestamo(
            prestamo_id=nuevo.id,
            numero_cuota=i,
            fecha_vencimiento=fv,
            monto_cuota=cuota,
            capital=capital_cuota,
            interes=interes_cuota,
            saldo_restante=max(money(0), saldo_rest),
        ))

    session.flush()
    return True, nuevo


# ─────────────────────────────────────────────────────────────
# SCORE CREDITICIO
# ─────────────────────────────────────────────────────────────

def calcular_score(session, cliente_id):
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        return None

    prestamos = session.query(Prestamo).filter_by(cliente_id=cliente_id).all()
    cuotas_all = []
    for p in prestamos:
        cuotas_all.extend(p.cuotas)

    pagos_puntuales = sum(1 for c in cuotas_all if c.estado == "PAGADA" and
                          (c.fecha_pago and c.fecha_vencimiento and
                           c.fecha_pago.date() <= c.fecha_vencimiento))
    pagos_atrasados = sum(1 for c in cuotas_all if c.estado in ("VENCIDA",))
    prestamos_activos = sum(1 for p in prestamos if p.estado == "ACTIVO")

    # Saldo promedio de últimos movimientos
    movs = (session.query(Movimiento)
            .filter_by(cliente_id=cliente_id)
            .order_by(Movimiento.fecha.desc())
            .limit(30).all())
    saldo_prom = float(cliente.saldo)

    # Fórmula simple de score: base 500
    score = 500
    score += pagos_puntuales * 10
    score -= pagos_atrasados * 25
    if saldo_prom > 1000: score += 50
    if saldo_prom > 5000: score += 100
    if prestamos_activos > 2: score -= 30
    score = max(300, min(850, score))

    if   score >= 750: categoria = "Excelente"
    elif score >= 650: categoria = "Bueno"
    elif score >= 550: categoria = "Regular"
    elif score >= 450: categoria = "Malo"
    else:              categoria = "Muy Malo"

    # Guardar/actualizar
    sc = session.query(ScoreCredito).filter_by(cliente_id=cliente_id).first()
    if sc:
        sc.score = score
        sc.pagos_puntuales = pagos_puntuales
        sc.pagos_atrasados = pagos_atrasados
        sc.saldo_promedio = money(saldo_prom)
        sc.prestamos_activos = prestamos_activos
        sc.calculado_en = datetime.utcnow()
        sc.categoria = categoria
    else:
        sc = ScoreCredito(
            cliente_id=cliente_id, score=score,
            pagos_puntuales=pagos_puntuales, pagos_atrasados=pagos_atrasados,
            saldo_promedio=money(saldo_prom), prestamos_activos=prestamos_activos,
            categoria=categoria,
        )
        session.add(sc)

    cliente.score_credito = score
    session.flush()
    return sc


# ─────────────────────────────────────────────────────────────
# TARJETAS
# ─────────────────────────────────────────────────────────────

def emitir_tarjeta_debito(session, cliente_id):
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        return False, "Cliente no encontrado"
    venc = f"{(date.today().month):02d}/{str(date.today().year + 4)[2:]}"
    td = TarjetaDebito(cliente_id=cliente_id, vencimiento=venc)
    session.add(td)
    session.flush()
    return True, td


def emitir_tarjeta_credito(session, cliente_id, limite):
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        return False, "Cliente no encontrado"
    if cliente.score_credito and cliente.score_credito < 500:
        return False, f"Score crediticio insuficiente ({cliente.score_credito}). Mínimo requerido: 500"
    venc = f"{(date.today().month):02d}/{str(date.today().year + 3)[2:]}"
    tc = TarjetaCredito(
        cliente_id=cliente_id,
        vencimiento=venc,
        limite=money(limite),
    )
    session.add(tc)
    session.flush()
    return True, tc


# ─────────────────────────────────────────────────────────────
# AML — ANTI LAVADO DE DINERO
# ─────────────────────────────────────────────────────────────

UMBRAL_AML_MONTO = Decimal("10000.00")
UMBRAL_AML_TRANSFERENCIAS_DIA = 5

def verificar_aml(session, cliente_id, monto, tipo_operacion=""):
    alertas_generadas = []
    monto = money(monto)
    hoy = _hoy()

    # Regla 1: monto alto
    if monto >= UMBRAL_AML_MONTO:
        alerta = AlertaAML(
            cliente_id=cliente_id,
            tipo="MONTO_ALTO",
            descripcion=f"{tipo_operacion}: monto ${float(monto):,.2f} supera el umbral de ${float(UMBRAL_AML_MONTO):,.2f}",
            monto=monto,
            nivel="CRITICA",
        )
        session.add(alerta)
        alertas_generadas.append(alerta)

    # Regla 2: múltiples transferencias en el día
    if "transfer" in tipo_operacion.lower():
        count = (session.query(func.count(Movimiento.id))
                 .filter(Movimiento.cliente_id == cliente_id)
                 .filter(func.date(Movimiento.fecha) == hoy)
                 .filter(Movimiento.tipo.in_(["Transferencia Enviada", "Transferencia Recibida"]))
                 .scalar() or 0)
        if count >= UMBRAL_AML_TRANSFERENCIAS_DIA:
            alerta = AlertaAML(
                cliente_id=cliente_id,
                tipo="MULT_TRANSFERENCIAS",
                descripcion=f"{count + 1} transferencias en el día {hoy}",
                nivel="WARNING",
            )
            session.add(alerta)
            alertas_generadas.append(alerta)

    if alertas_generadas:
        session.flush()

    return alertas_generadas


def obtener_alertas_aml(session, estado="PENDIENTE"):
    q = session.query(AlertaAML)
    if estado != "TODAS":
        q = q.filter(AlertaAML.estado == estado)
    return q.order_by(AlertaAML.fecha.desc()).all()


def revisar_alerta_aml(session, alerta_id, revisor, notas="", cerrar=False):
    a = session.query(AlertaAML).filter_by(id=alerta_id).first()
    if not a:
        return False, "Alerta no encontrada"
    a.estado = "CERRADA" if cerrar else "REVISADA"
    a.revisado_por = revisor
    a.notas = notas
    session.flush()
    return True, a


# ─────────────────────────────────────────────────────────────
# CIERRE DIARIO
# ─────────────────────────────────────────────────────────────

def realizar_cierre_diario(session, usuario_nombre, notas=""):
    hoy = _hoy()
    if session.query(CierreDiario).filter_by(fecha=hoy).first():
        return False, "Ya se realizó el cierre de hoy"

    # Totales del día
    def _suma(tipos):
        r = (session.query(func.coalesce(func.sum(Movimiento.monto), 0))
             .filter(func.date(Movimiento.fecha) == hoy)
             .filter(Movimiento.tipo.in_(tipos))
             .scalar())
        return money(r)

    total_dep   = _suma(["Deposito"])
    total_ret   = _suma(["Retiro"])
    total_trf   = _suma(["Transferencia Enviada"])
    total_pres  = _suma(["Prestamo"])
    total_pagos = _suma(["Pago Prestamo"])

    # Caja
    from contabilidad import caja_real
    caja_fin = money(caja_real(session))
    caja_ini = money(caja_fin - total_dep + total_ret)

    cierre = CierreDiario(
        fecha=hoy,
        total_depositos=total_dep,
        total_retiros=total_ret,
        total_transferencias=total_trf,
        total_prestamos=total_pres,
        total_pagos=total_pagos,
        caja_inicial=caja_ini,
        caja_final=caja_fin,
        realizado_por=usuario_nombre,
        notas=notas,
    )
    session.add(cierre)

    # Asiento de cierre
    if total_dep > 0 or total_ret > 0:
        registrar(session,
                  debitos=[("Caja General", money(0.01))],
                  creditos=[("Caja General", money(0.01))],
                  descripcion=f"Cierre diario {hoy} - Dep:{total_dep} Ret:{total_ret}")

    session.flush()
    return True, cierre


# ─────────────────────────────────────────────────────────────
# BALANCE GENERAL
# ─────────────────────────────────────────────────────────────

def generar_balance_general(session):

    cuentas = session.query(CuentaContable).all()

    activos = []
    pasivos = []
    patrimonio = []
    ingresos = []
    gastos = []

    for c in cuentas:

        saldo = c.saldo(session)

        if c.categoria == "ACTIVO":

            if c.nombre == "Provision Incobrables":
                activos.append((c.nombre, -saldo))
            else:
                 activos.append((c.nombre, saldo))

        elif c.categoria == "PASIVO":
            pasivos.append((c.nombre, saldo))

        elif c.categoria == "PATRIMONIO":
            patrimonio.append((c.nombre, saldo))

        elif c.categoria == "INGRESO":
            ingresos.append((c.nombre, saldo))

        elif c.categoria == "GASTO":
            gastos.append((c.nombre, saldo))

    total_activo = sum(v for _, v in activos)

    total_pasivo = sum(v for _, v in pasivos)

    total_patrimonio = (
        sum(v for _, v in patrimonio)
        + sum(v for _, v in ingresos)
        - sum(v for _, v in gastos)
    )

    return {
        "activos": activos,
        "pasivos": pasivos,
        "patrimonio": patrimonio,
        "ingresos": ingresos,
        "gastos": gastos,
        "total_activo": total_activo,
        "total_pasivo": total_pasivo,
        "total_patrimonio": total_patrimonio,
        "ecuacion_ok":
            round(total_activo, 2)
            ==
            round(total_pasivo + total_patrimonio, 2),
        "fecha": date.today(),
    }


# ─────────────────────────────────────────────────────────────
# ESTADO DE RESULTADOS
# ─────────────────────────────────────────────────────────────

def generar_estado_resultados(session, desde=None, hasta=None):
    cuentas = session.query(CuentaContable).all()
    ingresos = [(c.nombre, c.saldo(session)) for c in cuentas if c.categoria == "INGRESO"]
    gastos   = [(c.nombre, c.saldo(session)) for c in cuentas if c.categoria == "GASTO"]

    total_ingresos = sum(v for _, v in ingresos)
    total_gastos   = sum(v for _, v in gastos)
    utilidad       = total_ingresos - total_gastos

    return {
        "ingresos": ingresos,
        "gastos": gastos,
        "total_ingresos": total_ingresos,
        "total_gastos": total_gastos,
        "utilidad": utilidad,
        "desde": desde or "Inicio",
        "hasta": hasta or date.today(),
    }


# ─────────────────────────────────────────────────────────────
# SOCIOS (Caja de Crédito)
# ─────────────────────────────────────────────────────────────

def registrar_socio(session, cliente_id, aporte_inicial=0):
    if session.query(Socio).filter_by(cliente_id=cliente_id).first():
        return False, "El cliente ya es socio"
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        return False, "Cliente no encontrado"

    num = f"SOC-{datetime.now().strftime('%Y')}-{''.join(random.choices(string.digits, k=5))}"
    socio = Socio(cliente_id=cliente_id, numero_socio=num)
    session.add(socio)
    session.flush()

    if aporte_inicial > 0:
        ok, msg = registrar_aporte(session, socio.id, aporte_inicial, "ORDINARIO", "Aporte inicial")
        if not ok:
            return False, msg

    return True, socio


def registrar_aporte(session, socio_id, monto, tipo="ORDINARIO", descripcion=""):
    socio = session.query(Socio).filter_by(id=socio_id).first()
    if not socio:
        return False, "Socio no encontrado"
    monto = money(monto)
    if monto <= 0:
        return False, "El monto debe ser positivo"

    aporte = AporteSocio(socio_id=socio_id, monto=monto, tipo=tipo, descripcion=descripcion)
    session.add(aporte)
    socio.aporte_total = money(socio.aporte_total or 0) + monto

    # Asiento: Caja / Capital Social
    registrar(session,
              debitos=[("Caja General", monto)],
              creditos=[("Capital Banco", monto)],
              descripcion=f"Aporte socio #{socio.numero_socio} - {tipo}")
    session.flush()
    return True, aporte


# ─────────────────────────────────────────────────────────────
# HISTORIAL COMPLETO DEL CLIENTE
# ─────────────────────────────────────────────────────────────

def historial_cliente(session, cliente_id):
    cliente = session.query(Cliente).filter_by(id=cliente_id).first()
    if not cliente:
        return None

    movimientos = (session.query(Movimiento)
                   .filter_by(cliente_id=cliente_id)
                   .order_by(Movimiento.fecha.desc()).all())

    prestamos = (session.query(Prestamo)
                 .filter_by(cliente_id=cliente_id)
                 .order_by(Prestamo.fecha.desc()).all())

    tarjetas_d = session.query(TarjetaDebito).filter_by(cliente_id=cliente_id).all()
    tarjetas_c = session.query(TarjetaCredito).filter_by(cliente_id=cliente_id).all()
    depositos_p = session.query(DepositoPlazoFijo).filter_by(cliente_id=cliente_id).all()
    socio = session.query(Socio).filter_by(cliente_id=cliente_id).first()
    score = session.query(ScoreCredito).filter_by(cliente_id=cliente_id).first()
    beneficiarios = session.query(Beneficiario).filter_by(cliente_id=cliente_id, activo=True).all()

    return {
        "cliente": cliente,
        "movimientos": movimientos,
        "prestamos": prestamos,
        "tarjetas_debito": tarjetas_d,
        "tarjetas_credito": tarjetas_c,
        "depositos_plazo": depositos_p,
        "socio": socio,
        "score": score,
        "beneficiarios": beneficiarios,
    }


# ─────────────────────────────────────────────────────────────
# ACTUALIZAR CUOTAS VENCIDAS (mora automática)
# ─────────────────────────────────────────────────────────────

def actualizar_cuotas_vencidas(session):
    hoy = _hoy()
    cuotas = (session.query(CuotaPrestamo)
              .filter(CuotaPrestamo.estado == "PENDIENTE")
              .filter(CuotaPrestamo.fecha_vencimiento < hoy)
              .all())
    for c in cuotas:
        c.estado = "VENCIDA"
    session.flush()
    return len(cuotas)
