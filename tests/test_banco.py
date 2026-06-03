"""
test_banco.py — Suite completa de tests para BancoAppSistemas
=============================================================
Cubre los 5 módulos implementados:
  1. operaciones.py  — depósito, retiro, transferencia, préstamo, pago
  2. alertas.py      — saldo bajo, caja baja, préstamos vencidos, login fallido
  3. exportar.py     — PDF estado de cuenta, CSV movimientos, amortización
  4. contabilidad.py — partida doble, reconciliación
  5. auth.py         — login, roles, permisos, usuarios

CÓMO EJECUTAR:
  pip install pytest sqlalchemy bcrypt reportlab
  cd BancoAppSistemas
  pytest tests/test_banco.py -v

Para ver solo un módulo:
  pytest tests/test_banco.py -v -k "test_deposito"
  pytest tests/test_banco.py -v -k "alertas"
  pytest tests/test_banco.py -v -k "exportar"

Para ver el resumen sin verbose:
  pytest tests/test_banco.py
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# ── Importar módulos del banco ────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    BaseLocal, Cliente, Movimiento, Prestamo, CuotaPrestamo,
    ConfigBanco, Usuario, AuditLog,
)
from contabilidad import inicializar_contabilidad, saldo_cuenta, caja_real, reconciliar
from operaciones import (
    crear_cliente, depositar, retirar, transferir,
    otorgar_prestamo, pagar_prestamo, devengar_interes,
    editar_cliente, suspender_cliente, reactivar_cliente, cerrar_cuenta,
    tasa_deposito, tasa_transferencia, tasa_prestamo,
)
from auth import (
    login, crear_usuario, hash_password, tiene_permiso,
    toggle_usuario, cambiar_rol, cambiar_password,
    registrar_primer_admin, PERMISOS,
)
from alertas import (
    verificar_saldos_bajos, verificar_caja_baja,
    verificar_prestamos_por_vencer, verificar_prestamos_vencidos,
    verificar_intentos_login, verificar_mora_activa,
    calcular_mora, obtener_todas_alertas,
    NIVEL_ERROR, NIVEL_WARNING,
)
from exportar import (
    generar_estado_cuenta_pdf, generar_comprobante_pdf,
    generar_amortizacion_pdf, generar_movimientos_csv,
    generar_balance_csv, generar_balance_pdf,
)


# ══════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def session():
    """Base de datos en memoria limpia para cada test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False}
    )
    BaseLocal.metadata.create_all(engine)
    s = Session(engine)
    inicializar_contabilidad(s)
    yield s
    s.close()


@pytest.fixture
def cliente_a(session):
    """Cliente A con $1000 de saldo inicial."""
    ok, msg = crear_cliente(session, "Ana Garcia", "ahorro", 1000.0)
    assert ok, f"No se pudo crear cliente A: {msg}"
    return session.query(Cliente).filter_by(nombre="Ana Garcia").first()


@pytest.fixture
def cliente_b(session):
    """Cliente B con $500 de saldo inicial."""
    ok, msg = crear_cliente(session, "Bob Torres", "corriente", 500.0)
    assert ok, f"No se pudo crear cliente B: {msg}"
    return session.query(Cliente).filter_by(nombre="Bob Torres").first()


@pytest.fixture
def admin_user(session):
    """Usuario administrador."""
    ok, _ = registrar_primer_admin(session, "admin", "Administrador", "admin123")
    assert ok
    return session.query(Usuario).filter_by(username="admin").first()


@pytest.fixture
def prestamo_activo(session, cliente_a):
    """Préstamo activo de $1000 a 12 meses para cliente A."""
    # Primero depositar más para que el cliente pueda tener el préstamo
    depositar(session, cliente_a.id, 2000.0)
    ok, msg = otorgar_prestamo(session, cliente_a.id, 1000.0, 12)
    assert ok, f"No se pudo crear préstamo: {msg}"
    session.refresh(cliente_a)
    return (session.query(Prestamo)
            .filter_by(cliente_id=cliente_a.id, estado="ACTIVO")
            .first())


# ══════════════════════════════════════════════════════════════
# 1. TESTS DE CLIENTES
# ══════════════════════════════════════════════════════════════

class TestClientes:

    def test_crear_cliente_basico(self, session):
        ok, msg = crear_cliente(session, "Maria Lopez", "ahorro", 0.0)
        assert ok
        c = session.query(Cliente).filter_by(nombre="Maria Lopez").first()
        assert c is not None
        assert c.estado == "ACTIVO"
        assert float(c.saldo) == 0.0

    def test_crear_cliente_saldo_inicial(self, session):
        ok, msg = crear_cliente(session, "Pedro Ramos", "ahorro", 500.0)
        assert ok
        c = session.query(Cliente).filter_by(nombre="Pedro Ramos").first()
        # El saldo inicial de $500 no tiene comisión de apertura
        assert float(c.saldo) == 500.0

    def test_crear_cliente_nombre_duplicado(self, session, cliente_a):
        ok, msg = crear_cliente(session, "Ana Garcia", "ahorro", 0.0)
        assert not ok
        assert "existe" in msg.lower()

    def test_crear_cliente_saldo_negativo(self, session):
        ok, msg = crear_cliente(session, "Test User", "ahorro", -100.0)
        assert not ok

    def test_editar_cliente(self, session, cliente_a):
        ok, msg = editar_cliente(session, cliente_a.id, telefono="555-1234", email="ana@test.com")
        assert ok
        session.refresh(cliente_a)
        assert cliente_a.telefono == "555-1234"
        assert cliente_a.email == "ana@test.com"

    def test_suspender_y_reactivar(self, session, cliente_a):
        ok, _ = suspender_cliente(session, cliente_a.id, "Prueba")
        assert ok
        session.refresh(cliente_a)
        assert cliente_a.estado == "SUSPENDIDO"

        ok, _ = reactivar_cliente(session, cliente_a.id)
        assert ok
        session.refresh(cliente_a)
        assert cliente_a.estado == "ACTIVO"

    def test_cerrar_cuenta_con_saldo(self, session, cliente_a):
        """No se puede cerrar si tiene saldo > 0."""
        ok, msg = cerrar_cuenta(session, cliente_a.id)
        assert not ok
        assert "saldo" in msg.lower()

    def test_cerrar_cuenta_vacia(self, session):
        crear_cliente(session, "Cliente Cero", "ahorro", 0.0)
        c = session.query(Cliente).filter_by(nombre="Cliente Cero").first()
        ok, _ = cerrar_cuenta(session, c.id)
        assert ok
        session.refresh(c)
        assert c.estado == "CERRADO"


# ══════════════════════════════════════════════════════════════
# 2. TESTS DE OPERACIONES BANCARIAS
# ══════════════════════════════════════════════════════════════

class TestDeposito:

    def test_deposito_basico(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        ok, msg = depositar(session, cliente_a.id, 100.0)
        assert ok
        session.refresh(cliente_a)
        # El cliente recibe 98% (2% comisión)
        tasa = float(tasa_deposito(session))
        neto_esperado = saldo_antes + (100.0 * (1 - tasa))
        assert abs(float(cliente_a.saldo) - neto_esperado) < 0.01

    def test_deposito_registra_movimiento(self, session, cliente_a):
        depositar(session, cliente_a.id, 200.0)
        movs = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Deposito"
        ).all()
        assert len(movs) >= 1

    def test_deposito_cuenta_suspendida(self, session, cliente_a):
        suspender_cliente(session, cliente_a.id)
        ok, msg = depositar(session, cliente_a.id, 100.0)
        assert not ok
        assert "suspendido" in msg.lower()

    def test_deposito_monto_minimo(self, session, cliente_a):
        ok, msg = depositar(session, cliente_a.id, 0.5)
        assert not ok

    def test_deposito_genera_ingreso_banco(self, session, cliente_a):
        comisiones_antes = saldo_cuenta(session, "Ingresos Comisiones")
        depositar(session, cliente_a.id, 1000.0)
        comisiones_despues = saldo_cuenta(session, "Ingresos Comisiones")
        tasa = float(tasa_deposito(session))
        assert abs((comisiones_despues - comisiones_antes) - 1000 * tasa) < 0.01


class TestRetiro:

    def test_retiro_basico(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        ok, msg = retirar(session, cliente_a.id, 100.0)
        assert ok
        session.refresh(cliente_a)
        assert abs(float(cliente_a.saldo) - (saldo_antes - 100.0)) < 0.01

    def test_retiro_saldo_insuficiente(self, session, cliente_a):
        ok, msg = retirar(session, cliente_a.id, 999999.0)
        assert not ok
        assert "insuficiente" in msg.lower()

    def test_retiro_total(self, session, cliente_a):
        saldo = float(cliente_a.saldo)
        ok, _ = retirar(session, cliente_a.id, saldo)
        assert ok
        session.refresh(cliente_a)
        assert float(cliente_a.saldo) == 0.0

    def test_retiro_registra_movimiento(self, session, cliente_a):
        retirar(session, cliente_a.id, 50.0)
        movs = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Retiro"
        ).all()
        assert len(movs) == 1
        assert float(movs[0].monto) == 50.0


class TestTransferencia:

    def test_transferencia_basica(self, session, cliente_a, cliente_b):
        saldo_a = float(cliente_a.saldo)
        saldo_b = float(cliente_b.saldo)
        tasa = float(tasa_transferencia(session))

        ok, msg = transferir(session, cliente_a.id, cliente_b.id, 100.0)
        assert ok

        session.refresh(cliente_a)
        session.refresh(cliente_b)

        # B recibe exactamente $100
        assert abs(float(cliente_b.saldo) - (saldo_b + 100.0)) < 0.01
        # A paga $100 + comisión
        assert abs(float(cliente_a.saldo) - (saldo_a - 100.0 * (1 + tasa))) < 0.01

    def test_transferencia_a_si_mismo(self, session, cliente_a):
        ok, msg = transferir(session, cliente_a.id, cliente_a.id, 50.0)
        assert not ok

    def test_transferencia_saldo_insuficiente(self, session, cliente_a, cliente_b):
        ok, msg = transferir(session, cliente_a.id, cliente_b.id, 999999.0)
        assert not ok
        assert "insuficiente" in msg.lower()

    def test_transferencia_doble_movimiento(self, session, cliente_a, cliente_b):
        transferir(session, cliente_a.id, cliente_b.id, 50.0)
        enviados  = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Transferencia Enviada").count()
        recibidos = session.query(Movimiento).filter_by(
            cliente_id=cliente_b.id, tipo="Transferencia Recibida").count()
        assert enviados >= 1
        assert recibidos >= 1


# ══════════════════════════════════════════════════════════════
# 3. TESTS DE PRÉSTAMOS
# ══════════════════════════════════════════════════════════════

class TestPrestamos:

    def test_otorgar_prestamo_basico(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        ok, msg = otorgar_prestamo(session, cliente_a.id, 500.0, 12)
        assert ok
        session.refresh(cliente_a)
        # El dinero del préstamo se acredita al cliente
        assert float(cliente_a.saldo) > saldo_antes

    def test_prestamo_crea_cuotas(self, session, cliente_a):
        otorgar_prestamo(session, cliente_a.id, 500.0, 12)
        p = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).first()
        assert p is not None
        assert len(p.cuotas) == 12

    def test_prestamo_tiene_fecha_vencimiento(self, session, cliente_a):
        otorgar_prestamo(session, cliente_a.id, 500.0, 6)
        p = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).first()
        assert p.fecha_vencimiento is not None
        assert p.plazo_meses == 6

    def test_prestamo_monto_minimo(self, session, cliente_a):
        ok, msg = otorgar_prestamo(session, cliente_a.id, 50.0, 12)
        assert not ok
        assert "mínimo" in msg.lower()

    def test_pagar_prestamo_parcial(self, session, prestamo_activo, cliente_a):
        p = prestamo_activo
        # Primero pagamos todos los intereses para que el siguiente pago baje el capital
        interes_pendiente = float(p.interes) - float(p.interes_pagado)
        if interes_pendiente > 0:
            ok, msg = pagar_prestamo(session, cliente_a.id, interes_pendiente)
            assert ok, f"No se pudo pagar intereses: {msg}"
            session.refresh(p)

        # Ahora un pago de capital
        saldo_pendiente_antes = float(p.saldo_pendiente)
        session.refresh(cliente_a)
        if float(cliente_a.saldo) >= 100:
            ok, msg = pagar_prestamo(session, cliente_a.id, 100.0)
            assert ok, f"Fallo pago capital: {msg}"
            session.refresh(p)
            assert float(p.saldo_pendiente) < saldo_pendiente_antes
        else:
            # Si no tiene saldo suficiente, al menos verificamos que el pago de intereses funcionó
            assert float(p.interes_pagado) > 0

    def test_pagar_prestamo_completo(self, session, cliente_a):
        """Pagar el préstamo completo debe marcarlo como PAGADO."""
        depositar(session, cliente_a.id, 5000.0)
        session.refresh(cliente_a)
        ok, _ = otorgar_prestamo(session, cliente_a.id, 500.0, 12)
        assert ok

        p = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).first()
        deuda_total = float(p.saldo_pendiente) + float(p.interes) - float(p.interes_pagado)

        ok, msg = pagar_prestamo(session, cliente_a.id, deuda_total)
        assert ok
        session.refresh(p)
        assert p.estado == "PAGADO"

    def test_multiples_prestamos_activos(self, session, cliente_a):
        """Un cliente puede tener más de un préstamo activo."""
        depositar(session, cliente_a.id, 5000.0)
        ok1, _ = otorgar_prestamo(session, cliente_a.id, 300.0, 6)
        ok2, _ = otorgar_prestamo(session, cliente_a.id, 200.0, 12)
        assert ok1 and ok2
        activos = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).count()
        assert activos == 2

    def test_devengar_interes(self, session, cliente_a):
        depositar(session, cliente_a.id, 2000.0)
        otorgar_prestamo(session, cliente_a.id, 1000.0, 12)
        ok, msg = devengar_interes(session)
        assert ok
        assert "préstamo" in msg.lower()

    def test_pago_supera_deuda(self, session, prestamo_activo, cliente_a):
        p = prestamo_activo
        deuda = float(p.saldo_pendiente) + float(p.interes)
        ok, msg = pagar_prestamo(session, cliente_a.id, deuda + 1000.0)
        assert not ok
        assert "supera" in msg.lower()


# ══════════════════════════════════════════════════════════════
# 4. TESTS DE CONTABILIDAD
# ══════════════════════════════════════════════════════════════

class TestContabilidad:

    def test_balance_cuadrado_inicial(self, session):
        errores, _ = reconciliar(session)
        assert errores == 0

    def test_balance_cuadrado_tras_deposito(self, session, cliente_a):
        depositar(session, cliente_a.id, 500.0)
        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)

    def test_balance_cuadrado_tras_transferencia(self, session, cliente_a, cliente_b):
        transferir(session, cliente_a.id, cliente_b.id, 100.0)
        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)

    def test_balance_cuadrado_tras_prestamo(self, session, cliente_a):
        otorgar_prestamo(session, cliente_a.id, 500.0, 12)
        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)

    def test_balance_cuadrado_tras_pago_prestamo(self, session, prestamo_activo, cliente_a):
        pagar_prestamo(session, cliente_a.id, 100.0)
        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)

    def test_caja_incrementa_con_deposito(self, session, cliente_a):
        caja_antes = caja_real(session)
        depositar(session, cliente_a.id, 200.0)
        caja_despues = caja_real(session)
        assert caja_despues > caja_antes

    def test_caja_reduce_con_retiro(self, session, cliente_a):
        caja_antes = caja_real(session)
        retirar(session, cliente_a.id, 100.0)
        caja_despues = caja_real(session)
        assert caja_despues < caja_antes

    def test_stress_100_operaciones(self, session, cliente_a, cliente_b):
        """100 operaciones aleatorias deben dejar el balance cuadrado."""
        import random
        random.seed(42)
        for _ in range(100):
            op = random.choice(["dep", "ret", "tra"])
            if op == "dep":
                depositar(session, cliente_a.id, random.randint(10, 200))
            elif op == "ret":
                session.refresh(cliente_a)
                if float(cliente_a.saldo) > 10:
                    retirar(session, cliente_a.id, 10)
            else:
                session.refresh(cliente_a)
                session.refresh(cliente_b)
                if float(cliente_a.saldo) > 20:
                    transferir(session, cliente_a.id, cliente_b.id, 10)

        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 5. TESTS DE TASAS CONFIGURABLES
# ══════════════════════════════════════════════════════════════

class TestTasasConfigurables:

    def test_tasa_deposito_default(self, session):
        tasa = tasa_deposito(session)
        assert tasa == Decimal("0.02")

    def test_tasa_transferencia_default(self, session):
        tasa = tasa_transferencia(session)
        assert tasa == Decimal("0.01")

    def test_tasa_prestamo_default(self, session):
        tasa = tasa_prestamo(session)
        assert tasa == Decimal("0.10")

    def test_cambiar_tasa_deposito(self, session, cliente_a):
        """Cambiar la tasa a 5% y verificar que aplica en el siguiente depósito."""
        session.add(ConfigBanco(clave="tasa_deposito", valor="0.05"))
        session.commit()

        saldo_antes = float(cliente_a.saldo)
        ok, _ = depositar(session, cliente_a.id, 100.0)
        assert ok
        session.refresh(cliente_a)
        # Con 5% el neto debe ser $95
        assert abs(float(cliente_a.saldo) - (saldo_antes + 95.0)) < 0.01

    def test_cambiar_tasa_transferencia(self, session, cliente_a, cliente_b):
        """Cambiar la tasa de transferencia a 2%."""
        session.add(ConfigBanco(clave="tasa_transferencia", valor="0.02"))
        session.commit()

        saldo_b = float(cliente_b.saldo)
        ok, _ = transferir(session, cliente_a.id, cliente_b.id, 100.0)
        assert ok
        session.refresh(cliente_b)
        # B recibe $100 exactos
        assert abs(float(cliente_b.saldo) - (saldo_b + 100.0)) < 0.01

    def test_tasa_cero_deposito(self, session, cliente_a):
        """Con tasa 0% el cliente recibe el 100%."""
        session.add(ConfigBanco(clave="tasa_deposito", valor="0.00"))
        session.commit()

        saldo_antes = float(cliente_a.saldo)
        depositar(session, cliente_a.id, 100.0)
        session.refresh(cliente_a)
        assert abs(float(cliente_a.saldo) - (saldo_antes + 100.0)) < 0.01


# ══════════════════════════════════════════════════════════════
# 6. TESTS DE ALERTAS
# ══════════════════════════════════════════════════════════════

class TestAlertas:

    def test_saldo_bajo_detectado(self, session):
        """Cliente con $10 debe aparecer en alertas (mínimo default $50)."""
        crear_cliente(session, "Cliente Pobre", "ahorro", 10.0)
        alertas = verificar_saldos_bajos(session)
        nombres = [a["extra"]["nombre"] for a in alertas]
        assert "Cliente Pobre" in nombres

    def test_saldo_suficiente_no_alerta(self, session, cliente_a):
        """Cliente con $1000 no debe aparecer en alertas de saldo."""
        alertas = verificar_saldos_bajos(session)
        nombres = [a["extra"].get("nombre") for a in alertas]
        assert "Ana Garcia" not in nombres

    def test_caja_baja_detectada(self, session):
        alertas = verificar_caja_baja(session, caja_actual=500.0)
        assert len(alertas) == 1
        assert alertas[0]["nivel"] == NIVEL_ERROR

    def test_caja_suficiente_no_alerta(self, session):
        alertas = verificar_caja_baja(session, caja_actual=5000.0)
        assert len(alertas) == 0

    def test_prestamo_vencido_detectado(self, session, cliente_a):
        """Préstamo con fecha de vencimiento pasada debe generar alerta."""
        depositar(session, cliente_a.id, 2000.0)
        otorgar_prestamo(session, cliente_a.id, 500.0, 12)
        p = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).first()
        # Retrotraer la fecha de vencimiento
        p.fecha_vencimiento = datetime.utcnow().date() - timedelta(days=30)
        session.commit()

        alertas = verificar_prestamos_vencidos(session)
        clientes_en_alerta = [a["extra"]["cliente"] for a in alertas]
        assert "Ana Garcia" in clientes_en_alerta

    def test_prestamo_por_vencer_detectado(self, session, cliente_a):
        """Préstamo que vence en 3 días debe generar alerta."""
        depositar(session, cliente_a.id, 2000.0)
        otorgar_prestamo(session, cliente_a.id, 500.0, 12)
        p = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).first()
        p.fecha_vencimiento = datetime.utcnow().date() + timedelta(days=3)
        session.commit()

        alertas = verificar_prestamos_por_vencer(session)
        assert len(alertas) >= 1

    def test_login_fallido_detectado(self, session, admin_user):
        """3 intentos fallidos en 15 min deben generar alerta."""
        from auth import registrar_log
        for _ in range(3):
            log = AuditLog(
                username="hacker",
                rol="—",
                accion="LOGIN_FALLIDO",
                detalle="intento",
                resultado="ERROR",
                fecha=datetime.utcnow(),
            )
            session.add(log)
        session.commit()

        alertas = verificar_intentos_login(session)
        assert len(alertas) >= 1
        assert alertas[0]["nivel"] == NIVEL_ERROR

    def test_mora_calculada(self, session):
        """Calcular mora para prestamo vencido."""
        ok, _ = crear_cliente(session, "ClienteMora", "ahorro", 5000.0)
        assert ok
        c = session.query(Cliente).filter_by(nombre="Clientemora").first()
        depositar(session, c.id, 2000.0)
        otorgar_prestamo(session, c.id, 500.0, 12)
        p = session.query(Prestamo).filter_by(cliente_id=c.id, estado="ACTIVO").first()
        p_id  = p.id
        p.fecha_vencimiento = datetime.now().date() - timedelta(days=30)
        session.commit()

        # Nueva sesion limpia para evitar cache ORM
        from sqlalchemy.orm import Session as S2
        with S2(session.get_bind()) as s2:
            n, msg = calcular_mora(s2)
            assert n >= 1, f"got {n}: {msg}"
            rows = s2.execute(__import__("sqlalchemy").text(
                "SELECT mora_acumulada, dias_mora FROM prestamos WHERE id=:id"
            ), {"id": p_id}).fetchone()
            assert rows is not None
            assert float(rows[0]) > 0, f"mora_acumulada={rows[0]}"
            assert int(rows[1]) == 30
    def test_todas_alertas_orden(self, session, cliente_a):
        """Las alertas críticas (error) deben aparecer primero."""
        caja = caja_real(session)
        alertas = obtener_todas_alertas(session, caja)
        niveles = [a["nivel"] for a in alertas]
        # Si hay alertas, las de error deben estar antes que las de warning
        if NIVEL_ERROR in niveles and NIVEL_WARNING in niveles:
            idx_err  = min(i for i, n in enumerate(niveles) if n == NIVEL_ERROR)
            idx_warn = min(i for i, n in enumerate(niveles) if n == NIVEL_WARNING)
            assert idx_err < idx_warn

    def test_umbral_alerta_configurable(self, session):
        """Cambiar el umbral de saldo mínimo afecta las alertas."""
        crear_cliente(session, "Cliente Medio", "ahorro", 200.0)
        # Con el umbral default ($50) no hay alerta
        alertas_antes = verificar_saldos_bajos(session)
        nombres_antes = [a["extra"]["nombre"] for a in alertas_antes]
        assert "Cliente Medio" not in nombres_antes

        # Subir el umbral a $500
        session.add(ConfigBanco(clave="alerta_saldo_minimo", valor="500.00"))
        session.commit()

        alertas_despues = verificar_saldos_bajos(session)
        nombres_despues = [a["extra"]["nombre"] for a in alertas_despues]
        assert "Cliente Medio" in nombres_despues


# ══════════════════════════════════════════════════════════════
# 7. TESTS DE EXPORTACIÓN
# ══════════════════════════════════════════════════════════════

class TestExportacion:

    def test_estado_cuenta_pdf_genera_bytes(self, session, cliente_a):
        depositar(session, cliente_a.id, 100.0)
        movs = session.query(Movimiento).filter_by(cliente_id=cliente_a.id).all()
        pdf = generar_estado_cuenta_pdf(cliente_a, movs)
        assert isinstance(pdf, bytes)
        assert len(pdf) > 1000  # PDF real, no vacío
        assert pdf[:4] == b"%PDF"  # Magic bytes de PDF

    def test_estado_cuenta_pdf_sin_movimientos(self, session, cliente_a):
        """PDF debe generarse aunque no haya movimientos."""
        pdf = generar_estado_cuenta_pdf(cliente_a, [])
        assert isinstance(pdf, bytes)
        assert pdf[:4] == b"%PDF"

    def test_comprobante_pdf(self, session, cliente_a):
        depositar(session, cliente_a.id, 100.0)
        mov = session.query(Movimiento).filter_by(cliente_id=cliente_a.id).first()
        pdf = generar_comprobante_pdf(mov, cliente_a)
        assert isinstance(pdf, bytes)
        assert pdf[:4] == b"%PDF"

    def test_amortizacion_pdf(self, session, cliente_a):
        depositar(session, cliente_a.id, 2000.0)
        session.refresh(cliente_a)
        otorgar_prestamo(session, cliente_a.id, 1000.0, 12)
        p = session.query(Prestamo).filter_by(
            cliente_id=cliente_a.id, estado="ACTIVO"
        ).first()
        cuotas = sorted(p.cuotas, key=lambda c: c.numero_cuota)
        pdf = generar_amortizacion_pdf(p, cuotas, cliente_a)
        assert isinstance(pdf, bytes)
        assert pdf[:4] == b"%PDF"

    def test_movimientos_csv_bytes(self, session, cliente_a):
        depositar(session, cliente_a.id, 100.0)
        retirar(session, cliente_a.id, 50.0)
        movs = session.query(Movimiento).filter_by(cliente_id=cliente_a.id).all()
        csv_bytes = generar_movimientos_csv(movs, cliente_a)
        assert isinstance(csv_bytes, bytes)
        contenido = csv_bytes.decode("utf-8-sig")
        assert "Fecha" in contenido
        assert "Tipo" in contenido
        assert "Monto" in contenido

    def test_csv_contiene_movimientos(self, session, cliente_a):
        depositar(session, cliente_a.id, 250.0)
        movs = session.query(Movimiento).filter_by(cliente_id=cliente_a.id).all()
        csv_bytes = generar_movimientos_csv(movs, cliente_a)
        contenido = csv_bytes.decode("utf-8-sig")
        assert "Deposito" in contenido

    def test_balance_csv(self):
        cuentas = [
            {"nombre": "Caja General",      "categoria": "ACTIVO",  "saldo": 5000.0},
            {"nombre": "Capital Banco",      "categoria": "PATRIMONIO", "saldo": 10000.0},
            {"nombre": "Depositos Clientes", "categoria": "PASIVO",  "saldo": 3000.0},
        ]
        csv_bytes = generar_balance_csv(cuentas)
        assert isinstance(csv_bytes, bytes)
        contenido = csv_bytes.decode("utf-8-sig")
        assert "Caja General" in contenido
        assert "5000.00" in contenido

    def test_balance_pdf(self):
        cuentas = [
            {"nombre": "Caja General",      "categoria": "ACTIVO",  "saldo": 5000.0},
            {"nombre": "Capital Banco",      "categoria": "PATRIMONIO", "saldo": 10000.0},
        ]
        pdf = generar_balance_pdf(cuentas)
        assert isinstance(pdf, bytes)
        assert pdf[:4] == b"%PDF"


# ══════════════════════════════════════════════════════════════
# 8. TESTS DE AUTENTICACIÓN Y USUARIOS
# ══════════════════════════════════════════════════════════════

class TestAuth:

    def test_login_correcto(self, session, admin_user):
        u, msg = login(session, "admin", "admin123")
        assert u is not None
        assert u.rol == "ADMIN"

    def test_login_password_incorrecto(self, session, admin_user):
        u, msg = login(session, "admin", "wrong")
        assert u is None
        assert "incorrecta" in msg.lower()

    def test_login_usuario_no_existe(self, session):
        u, msg = login(session, "nadie", "123456")
        assert u is None

    def test_login_registra_log(self, session, admin_user):
        login(session, "admin", "admin123")
        log = session.query(AuditLog).filter_by(
            username="admin", accion="LOGIN"
        ).first()
        assert log is not None
        assert log.resultado == "OK"

    def test_login_fallido_registra_log(self, session, admin_user):
        login(session, "admin", "wrong")
        log = session.query(AuditLog).filter_by(
            username="admin", accion="LOGIN_FALLIDO"
        ).first()
        assert log is not None
        assert log.resultado == "ERROR"

    def test_permisos_admin(self):
        perms_admin = PERMISOS["ADMIN"]
        assert "gestionar_usuarios" in perms_admin
        assert "configurar_banco"   in perms_admin
        assert "ver_alertas"        in perms_admin
        assert "operaciones_bancarias" in perms_admin

    def test_permisos_cajero_no_configura(self):
        perms_cajero = PERMISOS["CAJERO"]
        assert "configurar_banco"   not in perms_cajero
        assert "gestionar_usuarios" not in perms_cajero

    def test_permisos_auditor_solo_lectura(self):
        perms_auditor = PERMISOS["AUDITOR"]
        assert "operaciones_bancarias" not in perms_auditor
        assert "gestionar_usuarios"    not in perms_auditor
        assert "ver_reportes"          in perms_auditor

    def test_crear_usuario_nuevo(self, session, admin_user):
        ok, msg = crear_usuario(session, admin_user, "cajero1", "Juan Cajero", "pass123", "CAJERO")
        assert ok
        u = session.query(Usuario).filter_by(username="cajero1").first()
        assert u is not None
        assert u.rol == "CAJERO"

    def test_no_crear_usuario_duplicado(self, session, admin_user):
        crear_usuario(session, admin_user, "cajero1", "Juan Cajero", "pass123", "CAJERO")
        ok, msg = crear_usuario(session, admin_user, "cajero1", "Otro Cajero", "pass456", "CAJERO")
        assert not ok

    def test_toggle_usuario(self, session, admin_user):
        crear_usuario(session, admin_user, "temp", "Temporal", "pass123", "CAJERO")
        temp = session.query(Usuario).filter_by(username="temp").first()
        ok, _ = toggle_usuario(session, admin_user, temp.id)
        assert ok
        session.refresh(temp)
        assert not temp.activo

    def test_cambiar_rol(self, session, admin_user):
        crear_usuario(session, admin_user, "user2", "Usuario Dos", "pass123", "CAJERO")
        u = session.query(Usuario).filter_by(username="user2").first()
        ok, _ = cambiar_rol(session, admin_user, u.id, "GERENTE")
        assert ok
        session.refresh(u)
        assert u.rol == "GERENTE"

    def test_usuario_desactivado_no_puede_login(self, session, admin_user):
        crear_usuario(session, admin_user, "inactivo", "Inactivo", "pass123", "CAJERO")
        u = session.query(Usuario).filter_by(username="inactivo").first()
        toggle_usuario(session, admin_user, u.id)  # desactivar

        resultado, msg = login(session, "inactivo", "pass123")
        assert resultado is None
        assert "desactivado" in msg.lower()


# ══════════════════════════════════════════════════════════════
# 9. TESTS DE INTEGRACIÓN (flujos completos)
# ══════════════════════════════════════════════════════════════

class TestIntegracion:

    def test_flujo_completo_cliente_nuevo(self, session):
        """Crear cliente → depositar → transferir → préstamo → pagar."""
        ok, _ = crear_cliente(session, "Integra A", "ahorro", 2000.0)
        assert ok
        ok, _ = crear_cliente(session, "Integra B", "ahorro", 100.0)
        assert ok

        a = session.query(Cliente).filter_by(nombre="Integra A").first()
        b = session.query(Cliente).filter_by(nombre="Integra B").first()

        # Depositar
        ok, _ = depositar(session, a.id, 500.0)
        assert ok

        # Transferir a B
        ok, _ = transferir(session, a.id, b.id, 200.0)
        assert ok

        # Préstamo
        ok, _ = otorgar_prestamo(session, a.id, 300.0, 6)
        assert ok

        # Pago parcial
        ok, _ = pagar_prestamo(session, a.id, 50.0)
        assert ok

        # Balance debe cuadrar
        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)

    def test_exportar_despues_de_operaciones(self, session):
        """Generar PDF y CSV después de varias operaciones."""
        crear_cliente(session, "Export Test", "ahorro", 1000.0)
        c = session.query(Cliente).filter_by(nombre="Export Test").first()

        depositar(session, c.id, 200.0)
        retirar(session,  c.id, 50.0)
        otorgar_prestamo(session, c.id, 300.0, 12)

        movs = session.query(Movimiento).filter_by(cliente_id=c.id).all()

        pdf = generar_estado_cuenta_pdf(c, movs)
        csv_b = generar_movimientos_csv(movs, c)

        assert pdf[:4] == b"%PDF"
        assert b"Export Test" in pdf or len(pdf) > 2000
        assert b"Export Test" in csv_b

    def test_alertas_despues_de_mora(self, session):
        """Prestamo vencido genera alertas y mora calculada."""
        ok, _ = crear_cliente(session, "Moroso", "ahorro", 3000.0)
        assert ok
        c = session.query(Cliente).filter_by(nombre="Moroso").first()
        depositar(session, c.id, 500.0)
        otorgar_prestamo(session, c.id, 500.0, 12)
        p = session.query(Prestamo).filter_by(cliente_id=c.id, estado="ACTIVO").first()
        p.fecha_vencimiento = datetime.now().date() - timedelta(days=60)
        session.commit()

        # Nueva sesion limpia
        from sqlalchemy.orm import Session as S2
        from sqlalchemy import text as sqlt
        with S2(session.get_bind()) as s2:
            n, msg = calcular_mora(s2)
            assert n >= 1, f"mora no calculada: {msg}"

            # Verificar directamente en BD
            row = s2.execute(sqlt(
                "SELECT mora_acumulada FROM prestamos WHERE estado='ACTIVO' AND mora_acumulada > 0"
            )).fetchone()
            assert row is not None, "Ninguna fila con mora > 0 en BD"

            # Alertas de prestamos vencidos
            alertas = verificar_prestamos_vencidos(s2)
            assert any(a["extra"]["cliente"] == "Moroso" for a in alertas), \
                "Moroso no aparece en alertas vencidos"

            # Alertas de mora activa (usa SQL directo internamente)
            mora_alertas = verificar_mora_activa(s2)
            assert any(a["extra"]["cliente"] == "Moroso" for a in mora_alertas), \
                f"Moroso no en mora_alertas. BD row={row}, alertas={mora_alertas}"
    def test_configuracion_afecta_operaciones(self, session):
        """Cambiar tasas y verificar que el balance sigue cuadrado."""
        # Subir tasa de depósito al 5%
        session.add(ConfigBanco(clave="tasa_deposito",      valor="0.05"))
        session.add(ConfigBanco(clave="tasa_transferencia", valor="0.02"))
        session.commit()

        crear_cliente(session, "Config A", "ahorro", 500.0)
        crear_cliente(session, "Config B", "ahorro", 500.0)
        a = session.query(Cliente).filter_by(nombre="Config A").first()
        b = session.query(Cliente).filter_by(nombre="Config B").first()

        depositar(session, a.id, 300.0)
        transferir(session, a.id, b.id, 50.0)

        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)
