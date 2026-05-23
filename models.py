"""
models.py — Modelos SQLAlchemy del Sistema Bancario
Incluye modelos locales (clientes, movimientos, préstamos)
y modelos contables (cuentas, asientos de diario).
"""

from sqlalchemy import (Column,Integer,String,Numeric,DateTime,ForeignKey,Text,CheckConstraint,)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

BaseLocal = declarative_base()


# ─────────────────────────────────────────────────────────────
# MODELOS OPERATIVOS
# ─────────────────────────────────────────────────────────────

class Cliente(BaseLocal):
    __tablename__ = "clientes"

    __table_args__ = (
        CheckConstraint('saldo >= 0', name='check_saldo_no_negativo'),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    nombre      = Column(String, nullable=False, unique=True)
    tipo        = Column(String, default="ahorro")
    saldo       = Column(Numeric(12,2), default=0.00)
    creado_en   = Column(DateTime, default=datetime.utcnow)

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