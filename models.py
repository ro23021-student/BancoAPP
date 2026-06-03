"""
models.py — Modelos SQLAlchemy del Sistema Bancario
Incluye modelos locales (clientes, movimientos, préstamos)
y modelos contables (cuentas, asientos de diario).
"""

from sqlalchemy import (Column,Integer,String,Numeric,Date,DateTime,ForeignKey,Text,CheckConstraint,Boolean,)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import random, string

BaseLocal = declarative_base()


# ─────────────────────────────────────────────────────────────
# MODELOS OPERATIVOS
# ─────────────────────────────────────────────────────────────

def _gen_num_cuenta():
    """Genera número de cuenta único: 4 letras + 8 dígitos, ej. BSIT-00283741"""
    letras = ''.join(random.choices(string.ascii_uppercase, k=4))
    digitos = ''.join(random.choices(string.digits, k=8))
    return f"{letras}-{digitos}"


class Cliente(BaseLocal):
    __tablename__ = "clientes"

    __table_args__ = (
        CheckConstraint('saldo >= 0', name='check_saldo_no_negativo'),
    )

    # ── Identificación ──
    id              = Column(Integer, primary_key=True, autoincrement=True)
    num_cuenta      = Column(String, unique=True, nullable=False, default=_gen_num_cuenta)
    nombre          = Column(String, nullable=False, unique=True)
    tipo            = Column(String, default="ahorro")   # ahorro | corriente
    estado          = Column(String, default="ACTIVO")   # ACTIVO | SUSPENDIDO | CERRADO

    # ── Datos personales ──
    documento       = Column(String)          # DUI, pasaporte, NIT…
    tipo_documento  = Column(String, default="DUI")
    telefono        = Column(String)
    email           = Column(String)
    direccion       = Column(Text)
    fecha_nacimiento= Column(String)          # guardado como string "YYYY-MM-DD"

    # ── Financiero ──
    saldo           = Column(Numeric(12,2), default=0.00)
    limite_credito  = Column(Numeric(12,2), default=0.00)

    # ── Auditoría ──
    creado_en       = Column(DateTime, default=datetime.utcnow)
    actualizado_en  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    motivo_cierre   = Column(Text)

    movimientos = relationship(
        "Movimiento",
        back_populates="cliente",
        cascade="all, delete-orphan"
    )

    prestamos   = relationship(
        "Prestamo",
        back_populates="cliente",
        cascade="all, delete-orphan"
    )

class Movimiento(BaseLocal):
    __tablename__ = "movimientos"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id  = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    tipo        = Column(String)
    monto       = Column(Numeric(12, 2))
    descripcion = Column(String)
    fecha       = Column(DateTime, default=datetime.now)
    cliente     = relationship("Cliente", back_populates="movimientos")


class Prestamo(BaseLocal):
    __tablename__ = "prestamos"

    __table_args__ = (
        CheckConstraint('saldo_pendiente >= 0', name='check_prestamo_no_negativo'),
    )

    id                = Column(Integer, primary_key=True, autoincrement=True)
    cliente_id        = Column(Integer, ForeignKey("clientes.id"), nullable=False)

    monto             = Column(Numeric(12,2))
    interes           = Column(Numeric(12,2))
    interes_devengado = Column(Numeric(12,2), default=0.00)
    interes_pagado    = Column(Numeric(12,2), default=0.00)
    saldo_pendiente   = Column(Numeric(12,2))

    fecha             = Column(DateTime, default=datetime.utcnow)
    estado            = Column(String, default="ACTIVO")

    cliente           = relationship("Cliente", back_populates="prestamos")

    cuotas            = relationship("CuotaPrestamo",cascade="all, delete-orphan")
    
    plazo_meses       = Column(Integer)

    cuota_mensual     = Column(Numeric(14,2))

    fecha_vencimiento = Column(Date)

    dias_mora         = Column(Integer, default=0)

    mora_acumulada    = Column(Numeric(14,2), default=0)



class CuotaPrestamo(BaseLocal):

    __tablename__ = "cuotas_prestamo"

    id                = Column(Integer, primary_key=True)

    prestamo_id       = Column(Integer,ForeignKey("prestamos.id"))

    numero_cuota      = Column(Integer)

    fecha_vencimiento = Column(Date)

    monto_cuota       = Column(Numeric(14,2))

    capital           = Column(Numeric(14,2))

    interes           = Column(Numeric(14,2))

    saldo_restante    = Column(Numeric(14,2))

    estado            = Column(String(20), default="PENDIENTE")

    prestamo = relationship(
        "Prestamo",
        back_populates="cuotas"
    )

# ─────────────────────────────────────────────────────────────
# MODELOS CONTABLES (partida doble propia, sin dependencia externa)
# ─────────────────────────────────────────────────────────────

class CuentaContable(BaseLocal):
    """
    Plan de cuentas simplificado.
    tipo_normal: 'D' = saldo normal débito | 'C' = saldo normal crédito
    """
    __tablename__ = "cuentas_contables"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    nombre      = Column(String, unique=True, nullable=False)
    categoria   = Column(String)   # ACTIVO / PASIVO / PATRIMONIO / INGRESO / GASTO
    tipo_normal = Column(String)   # 'D' o 'C'
    asientos    = relationship("LineaAsiento", back_populates="cuenta")

    def saldo(self, session):
        """
        Calcula el saldo de la cuenta según su tipo normal.
        Saldo D = débitos - créditos
        Saldo C = créditos - débitos
        """
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
    """Encabezado del asiento de diario (journal entry)."""
    __tablename__ = "asientos"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    fecha       = Column(DateTime, default=datetime.now)
    descripcion = Column(Text)
    lineas      = relationship("LineaAsiento", back_populates="asiento",
                               cascade="all, delete-orphan")


class LineaAsiento(BaseLocal):
    """Línea de débito o crédito dentro de un asiento."""
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
    """
    Usuario del sistema bancario.
    Roles: ADMIN | CAJERO | GERENTE | AUDITOR
    """
    __tablename__ = "usuarios"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    username         = Column(String, unique=True, nullable=False)
    nombre           = Column(String, nullable=False)
    password_hash    = Column(String, nullable=False)
    rol              = Column(String, nullable=False)   # ADMIN | CAJERO | GERENTE | AUDITOR
    activo           = Column(Boolean, default=True)
    creado_en        = Column(DateTime, default=datetime.utcnow)
    ultimo_login     = Column(DateTime)
    intentos_fallidos= Column(Integer, default=0)
    bloqueado_hasta  = Column(DateTime, nullable=True)

    logs = relationship("AuditLog", back_populates="usuario",
                        cascade="all, delete-orphan")


class AuditLog(BaseLocal):
    """Registro inmutable de cada acción realizada en el sistema."""
    __tablename__ = "audit_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    usuario_id  = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    username    = Column(String)          # desnormalizado para no perder si se borra usuario
    rol         = Column(String)
    accion      = Column(String)          # LOGIN | DEPOSITO | RETIRO | etc.
    detalle     = Column(Text)
    resultado   = Column(String)          # OK | ERROR
    fecha       = Column(DateTime, default=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="logs")