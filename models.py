"""
models.py — Modelos SQLAlchemy del Sistema Bancario
Versión ampliada con: TipoCuenta, Beneficiario, DepositoPlazoFijo,
Garantia, TarjetaDebito, TarjetaCredito, Sucursal, ATM, AlertaAML,
ScoreCredito, CierreDiario, Socio, AportesSocio, CuotaCredito.
"""

from sqlalchemy import (Column, Integer, String, Numeric, Date, DateTime,
                        ForeignKey, Text, CheckConstraint, Boolean, Enum)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import random, string

BaseLocal = declarative_base()


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _gen_num_cuenta():
    """Genera número de cuenta único: 4 letras + 8 dígitos, ej. BSIT-00283741"""
    letras  = ''.join(random.choices(string.ascii_uppercase, k=4))
    digitos = ''.join(random.choices(string.digits, k=8))
    return f"{letras}-{digitos}"


def _gen_num_trx():
    """Genera número de transacción: TRX-YYYYMMDD-NNNNNNNNNN (10 dígitos para evitar colisiones)"""
    hoy  = datetime.utcnow().strftime("%Y%m%d")
    rand = ''.join(random.choices(string.digits, k=10))
    return f"TRX-{hoy}-{rand}"


def _gen_num_tarjeta():
    """Genera número de tarjeta de 16 dígitos con prefijo 4111"""
    return "4111" + ''.join(random.choices(string.digits, k=12))


def _gen_cvv():
    return ''.join(random.choices(string.digits, k=3))


# ─────────────────────────────────────────────────────────────
# TIPOS DE CUENTA
# ─────────────────────────────────────────────────────────────

class TipoCuenta(BaseLocal):
    __tablename__ = "tipos_cuenta"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    nombre          = Column(String, unique=True, nullable=False)
    tasa_interes    = Column(Numeric(6, 4), default=0.0000)   # tasa anual
    saldo_minimo    = Column(Numeric(12, 2), default=0.00)
    cobra_comision  = Column(Boolean, default=False)
    descripcion     = Column(Text)
    activo          = Column(Boolean, default=True)

    clientes        = relationship("Cliente", back_populates="tipo_cuenta_rel")


# ─────────────────────────────────────────────────────────────
# MODELOS OPERATIVOS
# ─────────────────────────────────────────────────────────────

class Cliente(BaseLocal):
    __tablename__ = "clientes"

    __table_args__ = (
        CheckConstraint('saldo >= 0', name='check_saldo_no_negativo'),
    )

    # ── Identificación ──
    id               = Column(Integer, primary_key=True, autoincrement=True)
    num_cuenta       = Column(String, unique=True, nullable=False, default=_gen_num_cuenta)
    nombre           = Column(String, nullable=False, unique=True)
    tipo             = Column(String, default="Ahorro")   # nombre del tipo
    tipo_cuenta_id   = Column(Integer, ForeignKey("tipos_cuenta.id"), nullable=True)
    estado           = Column(String, default="ACTIVO")   # ACTIVO | SUSPENDIDO | CERRADO

    # ── Datos personales ──
    documento        = Column(String)
    tipo_documento   = Column(String, default="DUI")
    telefono         = Column(String)
    email            = Column(String)
    direccion        = Column(Text)
    fecha_nacimiento = Column(String)

    # ── KYC adicional ──
    nit              = Column(String)
    profesion        = Column(String)
    ingresos_mensuales = Column(Numeric(12, 2), default=0.00)
    kyc_completo     = Column(Boolean, default=False)

    # ── Financiero ──
    saldo            = Column(Numeric(12, 2), default=0.00)
    limite_credito   = Column(Numeric(12, 2), default=0.00)
    score_credito    = Column(Integer, default=500)   # 300-850

    # ── Auditoría ──
    creado_en        = Column(DateTime, default=datetime.utcnow)
    actualizado_en   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    motivo_cierre    = Column(Text)

    # ── Sucursal ──
    sucursal_id      = Column(Integer, ForeignKey("sucursales.id"), nullable=True)

    # Relaciones
    tipo_cuenta_rel  = relationship("TipoCuenta", back_populates="clientes")
    sucursal         = relationship("Sucursal", back_populates="clientes")
    movimientos      = relationship("Movimiento", back_populates="cliente", cascade="all, delete-orphan")
    prestamos        = relationship("Prestamo", back_populates="cliente", cascade="all, delete-orphan")
    beneficiarios    = relationship("Beneficiario", back_populates="cliente", cascade="all, delete-orphan")
    tarjetas_debito  = relationship("TarjetaDebito", back_populates="cliente", cascade="all, delete-orphan")
    tarjetas_credito = relationship("TarjetaCredito", back_populates="cliente", cascade="all, delete-orphan")
    depositos_plazo  = relationship("DepositoPlazoFijo", back_populates="cliente", cascade="all, delete-orphan")
    alertas_aml      = relationship("AlertaAML", back_populates="cliente", cascade="all, delete-orphan")


class Movimiento(BaseLocal):
    __tablename__ = "movimientos"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    num_trx       = Column(String, unique=True, default=_gen_num_trx)   # TRX-YYYYMMDD-NNNNNN
    cliente_id    = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    tipo          = Column(String)
    monto         = Column(Numeric(12, 2))
    descripcion   = Column(String)
    fecha         = Column(DateTime, default=datetime.utcnow)
    canal         = Column(String, default="VENTANILLA")  # VENTANILLA | ATM | EN_LINEA
    sucursal_id   = Column(Integer, ForeignKey("sucursales.id"), nullable=True)
    atm_id        = Column(Integer, ForeignKey("atms.id"), nullable=True)

    cliente       = relationship("Cliente", back_populates="movimientos")
    sucursal      = relationship("Sucursal")
    atm           = relationship("ATM")


class Prestamo(BaseLocal):
    __tablename__ = "prestamos"

    __table_args__ = (
        CheckConstraint('saldo_pendiente >= 0', name='check_prestamo_no_negativo'),
    )

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id          = Column(Integer, ForeignKey("clientes.id"), nullable=False)

    monto               = Column(Numeric(12, 2))
    interes             = Column(Numeric(12, 2))
    interes_devengado   = Column(Numeric(12, 2), default=0.00)
    interes_pagado      = Column(Numeric(12, 2), default=0.00)
    saldo_pendiente     = Column(Numeric(12, 2))

    fecha               = Column(DateTime, default=datetime.utcnow)
    estado              = Column(String, default="ACTIVO")  # ACTIVO | PAGADO | REFINANCIADO | CASTIGADO

    # Clasificación crediticia
    clasificacion       = Column(String, default="Personal")  # Personal | Vivienda | Comercio | Agropecuario

    plazo_meses         = Column(Integer)
    cuota_mensual       = Column(Numeric(14, 2))
    fecha_vencimiento   = Column(Date)
    dias_mora           = Column(Integer, default=0)
    mora_acumulada      = Column(Numeric(14, 2), default=0)

    # Refinanciamiento
    prestamo_origen_id  = Column(Integer, ForeignKey("prestamos.id"), nullable=True)

    # Sucursal donde se otorgó
    sucursal_id         = Column(Integer, ForeignKey("sucursales.id"), nullable=True)

    cliente             = relationship("Cliente", back_populates="prestamos")
    cuotas              = relationship("CuotaPrestamo", cascade="all, delete-orphan")
    garantias           = relationship("Garantia", back_populates="prestamo", cascade="all, delete-orphan")
    sucursal            = relationship("Sucursal")
    prestamo_origen     = relationship("Prestamo", remote_side="Prestamo.id")


class CuotaPrestamo(BaseLocal):
    __tablename__ = "cuotas_prestamo"

    id                = Column(Integer, primary_key=True)
    prestamo_id       = Column(Integer, ForeignKey("prestamos.id"))
    numero_cuota      = Column(Integer)
    fecha_vencimiento = Column(Date)
    monto_cuota       = Column(Numeric(14, 2))
    capital           = Column(Numeric(14, 2))
    interes           = Column(Numeric(14, 2))
    saldo_restante    = Column(Numeric(14, 2))
    estado            = Column(String(20), default="PENDIENTE")  # PENDIENTE | PAGADA | VENCIDA
    fecha_pago        = Column(DateTime, nullable=True)

    prestamo          = relationship("Prestamo", back_populates="cuotas")


# ─────────────────────────────────────────────────────────────
# BENEFICIARIOS FRECUENTES
# ─────────────────────────────────────────────────────────────

class Beneficiario(BaseLocal):
    __tablename__ = "beneficiarios"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    cuenta_destino  = Column(String, nullable=False)   # num_cuenta del destinatario
    nombre_destino  = Column(String)
    alias           = Column(String)
    activo          = Column(Boolean, default=True)
    creado_en       = Column(DateTime, default=datetime.utcnow)

    cliente         = relationship("Cliente", back_populates="beneficiarios")


# ─────────────────────────────────────────────────────────────
# DEPÓSITO A PLAZO FIJO
# ─────────────────────────────────────────────────────────────

class DepositoPlazoFijo(BaseLocal):
    __tablename__ = "depositos_plazo_fijo"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    num_certificado     = Column(String, unique=True, default=lambda: f"DPF-{datetime.now().strftime('%Y%m%d')}-{''.join(random.choices(string.digits, k=4))}")
    cliente_id          = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    monto               = Column(Numeric(12, 2))
    tasa_anual          = Column(Numeric(6, 4))
    plazo_meses         = Column(Integer)
    fecha_apertura      = Column(Date, default=datetime.utcnow)
    fecha_vencimiento   = Column(Date)
    interes_proyectado  = Column(Numeric(12, 2))
    monto_total         = Column(Numeric(12, 2))   # capital + interés al vencimiento
    estado              = Column(String, default="ACTIVO")  # ACTIVO | VENCIDO | RENOVADO | CANCELADO
    renovacion_automatica = Column(Boolean, default=False)

    cliente             = relationship("Cliente", back_populates="depositos_plazo")


# ─────────────────────────────────────────────────────────────
# GARANTÍAS DE PRÉSTAMO
# ─────────────────────────────────────────────────────────────

class Garantia(BaseLocal):
    __tablename__ = "garantias"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    prestamo_id     = Column(Integer, ForeignKey("prestamos.id"), nullable=False)
    tipo            = Column(String)   # vehiculo | casa | terreno | deposito | fiador
    descripcion     = Column(Text)
    valor_estimado  = Column(Numeric(12, 2))
    numero_registro = Column(String)   # matrícula, escritura, etc.
    activa          = Column(Boolean, default=True)
    creado_en       = Column(DateTime, default=datetime.utcnow)

    prestamo        = relationship("Prestamo", back_populates="garantias")


# ─────────────────────────────────────────────────────────────
# SCORE CREDITICIO
# ─────────────────────────────────────────────────────────────

class ScoreCredito(BaseLocal):
    __tablename__ = "score_crediticio"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id          = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    score               = Column(Integer)            # 300-850
    pagos_puntuales     = Column(Integer, default=0)
    pagos_atrasados     = Column(Integer, default=0)
    saldo_promedio      = Column(Numeric(12, 2), default=0)
    prestamos_activos   = Column(Integer, default=0)
    calculado_en        = Column(DateTime, default=datetime.utcnow)
    categoria           = Column(String)  # Excelente | Bueno | Regular | Malo | Muy Malo


# ─────────────────────────────────────────────────────────────
# TARJETAS
# ─────────────────────────────────────────────────────────────

class TarjetaDebito(BaseLocal):
    __tablename__ = "tarjetas_debito"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    numero          = Column(String, unique=True, default=_gen_num_tarjeta)
    vencimiento     = Column(String)   # MM/AA
    cvv             = Column(String, default=_gen_cvv)
    estado          = Column(String, default="ACTIVA")  # ACTIVA | BLOQUEADA | CANCELADA
    creado_en       = Column(DateTime, default=datetime.utcnow)

    cliente         = relationship("Cliente", back_populates="tarjetas_debito")


class TarjetaCredito(BaseLocal):
    __tablename__ = "tarjetas_credito"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    numero          = Column(String, unique=True, default=_gen_num_tarjeta)
    vencimiento     = Column(String)
    cvv             = Column(String, default=_gen_cvv)
    limite          = Column(Numeric(12, 2))
    saldo_usado     = Column(Numeric(12, 2), default=0.00)
    pago_minimo     = Column(Numeric(12, 2), default=0.00)
    fecha_corte     = Column(Integer, default=15)   # día del mes
    tasa_interes    = Column(Numeric(6, 4), default=0.2400)  # 24% anual
    estado          = Column(String, default="ACTIVA")
    creado_en       = Column(DateTime, default=datetime.utcnow)

    cliente         = relationship("Cliente", back_populates="tarjetas_credito")


# ─────────────────────────────────────────────────────────────
# SUCURSALES
# ─────────────────────────────────────────────────────────────

class Sucursal(BaseLocal):
    __tablename__ = "sucursales"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    nombre      = Column(String, unique=True, nullable=False)
    direccion   = Column(Text)
    telefono    = Column(String)
    activa      = Column(Boolean, default=True)
    saldo_caja  = Column(Numeric(12, 2), default=0.00)
    creado_en   = Column(DateTime, default=datetime.utcnow)

    clientes    = relationship("Cliente", back_populates="sucursal")
    atms        = relationship("ATM", back_populates="sucursal")


# ─────────────────────────────────────────────────────────────
# CAJEROS AUTOMÁTICOS (ATM)
# ─────────────────────────────────────────────────────────────

class ATM(BaseLocal):
    __tablename__ = "atms"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    sucursal_id     = Column(Integer, ForeignKey("sucursales.id"), nullable=True)
    ubicacion       = Column(String)
    estado          = Column(String, default="OPERATIVO")  # OPERATIVO | FUERA_SERVICIO | MANTENIMIENTO
    saldo_atm       = Column(Numeric(12, 2), default=0.00)
    creado_en       = Column(DateTime, default=datetime.utcnow)

    sucursal        = relationship("Sucursal", back_populates="atms")


# ─────────────────────────────────────────────────────────────
# AML — ALERTAS ANTI LAVADO DE DINERO
# ─────────────────────────────────────────────────────────────

class AlertaAML(BaseLocal):
    __tablename__ = "alertas_aml"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    tipo            = Column(String)   # MONTO_ALTO | MULT_TRANSFERENCIAS | PATRON_INUSUAL
    descripcion     = Column(Text)
    monto           = Column(Numeric(12, 2), nullable=True)
    estado          = Column(String, default="PENDIENTE")  # PENDIENTE | REVISADA | CERRADA
    nivel           = Column(String, default="WARNING")    # WARNING | CRITICA
    fecha           = Column(DateTime, default=datetime.utcnow)
    revisado_por    = Column(String, nullable=True)
    notas           = Column(Text, nullable=True)

    cliente         = relationship("Cliente", back_populates="alertas_aml")


# ─────────────────────────────────────────────────────────────
# CIERRE DIARIO
# ─────────────────────────────────────────────────────────────

class CierreDiario(BaseLocal):
    __tablename__ = "cierres_diarios"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    fecha               = Column(Date, unique=True)
    total_depositos     = Column(Numeric(12, 2), default=0.00)
    total_retiros       = Column(Numeric(12, 2), default=0.00)
    total_transferencias= Column(Numeric(12, 2), default=0.00)
    total_prestamos     = Column(Numeric(12, 2), default=0.00)
    total_pagos         = Column(Numeric(12, 2), default=0.00)
    caja_inicial        = Column(Numeric(12, 2), default=0.00)
    caja_final          = Column(Numeric(12, 2), default=0.00)
    realizado_por       = Column(String)
    notas               = Column(Text)
    creado_en           = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────
# SOCIOS (Caja de Crédito)
# ─────────────────────────────────────────────────────────────

class Socio(BaseLocal):
    __tablename__ = "socios"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id      = Column(Integer, ForeignKey("clientes.id"), unique=True)
    numero_socio    = Column(String, unique=True)
    fecha_ingreso   = Column(Date, default=datetime.utcnow)
    estado          = Column(String, default="ACTIVO")  # ACTIVO | RETIRADO | SUSPENDIDO
    aporte_total    = Column(Numeric(12, 2), default=0.00)
    creado_en       = Column(DateTime, default=datetime.utcnow)

    cliente         = relationship("Cliente")
    aportes         = relationship("AporteSocio", back_populates="socio", cascade="all, delete-orphan")


class AporteSocio(BaseLocal):
    __tablename__ = "aportes_socios"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    socio_id    = Column(Integer, ForeignKey("socios.id"), nullable=False)
    monto       = Column(Numeric(12, 2))
    fecha       = Column(DateTime, default=datetime.utcnow)
    tipo        = Column(String, default="ORDINARIO")  # ORDINARIO | EXTRAORDINARIO
    descripcion = Column(String)

    socio       = relationship("Socio", back_populates="aportes")


# ─────────────────────────────────────────────────────────────
# MODELOS CONTABLES
# ─────────────────────────────────────────────────────────────

class CuentaContable(BaseLocal):
    __tablename__ = "cuentas_contables"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    nombre      = Column(String, unique=True, nullable=False)
    categoria   = Column(String)   # ACTIVO / PASIVO / PATRIMONIO / INGRESO / GASTO
    tipo_normal = Column(String)   # 'D' o 'C'
    asientos    = relationship("LineaAsiento", back_populates="cuenta")

    def saldo(self, session):
        from sqlalchemy import func
        debitos  = session.query(func.coalesce(func.sum(LineaAsiento.debito),  0.0))\
                          .filter(LineaAsiento.cuenta_id == self.id).scalar()
        creditos = session.query(func.coalesce(func.sum(LineaAsiento.credito), 0.0))\
                          .filter(LineaAsiento.cuenta_id == self.id).scalar()
        if self.tipo_normal == 'D':
            return round(float(debitos) - float(creditos), 2)
        else:
            return round(float(creditos) - float(debitos), 2)


class Asiento(BaseLocal):
    __tablename__ = "asientos"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    fecha       = Column(DateTime, default=datetime.now)
    descripcion = Column(Text)
    lineas      = relationship("LineaAsiento", back_populates="asiento",
                               cascade="all, delete-orphan")


class LineaAsiento(BaseLocal):
    __tablename__ = "lineas_asiento"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    asiento_id = Column(Integer, ForeignKey("asientos.id"), nullable=False)
    cuenta_id  = Column(Integer, ForeignKey("cuentas_contables.id"), nullable=False)
    debito     = Column(Numeric(12, 2), default=0.0)
    credito    = Column(Numeric(12, 2), default=0.0)
    asiento    = relationship("Asiento", back_populates="lineas")
    cuenta     = relationship("CuentaContable", back_populates="asientos")


class ConfigBanco(BaseLocal):
    __tablename__ = "config_banco"
    clave = Column(String, primary_key=True)
    valor = Column(String)


# ─────────────────────────────────────────────────────────────
# AUTENTICACIÓN Y AUDITORÍA
# ─────────────────────────────────────────────────────────────

class Usuario(BaseLocal):
    __tablename__ = "usuarios"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    username          = Column(String, unique=True, nullable=False)
    nombre            = Column(String, nullable=False)
    password_hash     = Column(String, nullable=False)
    rol               = Column(String, nullable=False)
    activo            = Column(Boolean, default=True)
    creado_en         = Column(DateTime, default=datetime.utcnow)
    ultimo_login      = Column(DateTime)
    intentos_fallidos = Column(Integer, default=0)
    bloqueado_hasta   = Column(DateTime, nullable=True)
    sucursal_id       = Column(Integer, ForeignKey("sucursales.id"), nullable=True)

    logs              = relationship("AuditLog", back_populates="usuario",
                                     cascade="all, delete-orphan")


class AuditLog(BaseLocal):
    __tablename__ = "audit_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id  = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    username    = Column(String)
    rol         = Column(String)
    accion      = Column(String)
    detalle     = Column(Text)
    resultado   = Column(String)
    fecha       = Column(DateTime, default=datetime.utcnow)

    usuario     = relationship("Usuario", back_populates="logs")
