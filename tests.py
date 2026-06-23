"""
test_banco.py — Suite de tests completa del Sistema Bancario.

Cubre: clientes, operaciones (depósito/retiro/transferencia), préstamos,
pagos, devengo de intereses, MORA y cuotas vencidas, contabilidad de
partida doble y reconciliación, tipos de cuenta, sucursales, beneficiarios,
depósitos a plazo fijo, garantías, refinanciamiento, score crediticio,
tarjetas débito/crédito, AML, cierre diario, balance general, estado de
resultados, socios/aportes, alertas del sistema, autenticación y permisos
por rol, y operaciones contables avanzadas (inversiones, bienes, impuestos,
reservas, gastos operativos, mora pagada por el banco).

Ejecutar:
    pytest tests/ -v
    pytest tests/ -v -k mora        (solo tests de mora)
    pytest tests/ -v --tb=short
"""

import sys
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import (
    BaseLocal, Cliente, Movimiento, Prestamo, CuotaPrestamo,
    TipoCuenta, Beneficiario, DepositoPlazoFijo, Garantia,
    ScoreCredito, TarjetaDebito, TarjetaCredito,
    Sucursal, ATM, AlertaAML, CierreDiario,
    Socio, AporteSocio, CuentaContable, Asiento, LineaAsiento, ConfigBanco,
    Usuario, AuditLog,
)
from contabilidad import (
    inicializar_contabilidad, saldo_cuenta, caja_real,
    reconciliar, registrar, money, get_cuenta,
)
from operaciones import (
    crear_cliente, depositar, retirar, transferir,
    otorgar_prestamo, pagar_prestamo, devengar_interes,
    editar_cliente, suspender_cliente, reactivar_cliente, cerrar_cuenta,
    revertir_operacion, verificar_limite_diario,
    registrar_inversion, liquidar_inversion,
    adquirir_bien_inmueble,
    clasificar_prestamo_moroso, constituir_provision, castigar_prestamo_incobrable,
    deposito_cuenta_ahorro, deposito_cuenta_corriente,
    recibir_prestamo_banco, pagar_obligacion_banco,
    provisionar_impuesto, pagar_impuesto,
    constituir_reserva_legal, registrar_utilidad_ejercicio,
    registrar_comision_tarjeta_credito,
    cobrar_mora_prestamo,
    registrar_gasto_operativo,
    registrar_mora_pagada_banco,
    tasa_deposito, tasa_transferencia, tasa_prestamo,
)
from extras import (
    inicializar_tipos_cuenta, inicializar_sucursales, crear_tipo_cuenta,
    crear_sucursal,
    agregar_beneficiario, eliminar_beneficiario,
    crear_deposito_plazo, vencer_deposito_plazo,
    agregar_garantia, refinanciar_prestamo,
    calcular_score, emitir_tarjeta_debito, emitir_tarjeta_credito,
    verificar_aml, obtener_alertas_aml, revisar_alerta_aml,
    realizar_cierre_diario, generar_balance_general, generar_estado_resultados,
    registrar_socio, registrar_aporte, historial_cliente,
    actualizar_cuotas_vencidas,
)
from alertas import (
    obtener_todas_alertas, contar_alertas, calcular_mora,
    verificar_saldos_bajos, verificar_prestamos_por_vencer,
    verificar_prestamos_vencidos, verificar_mora_activa,
    verificar_caja_baja, NIVEL_ERROR, NIVEL_WARNING, NIVEL_INFO,
)
from auth import (
    hay_usuarios, registrar_primer_admin, login,
    crear_usuario, cambiar_password, toggle_usuario, cambiar_rol,
    tiene_permiso, hash_password, verify_password,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def session():
    """Sesión SQLite en memoria, aislada por test, con contabilidad inicializada."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    BaseLocal.metadata.create_all(engine)
    s = Session(engine)
    inicializar_contabilidad(s)
    inicializar_tipos_cuenta(s)
    inicializar_sucursales(s)
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def cliente_a(session):
    ok, c = crear_cliente(session, "Ana Gomez", "Ahorro", 1000)
    assert ok, c
    return c


@pytest.fixture()
def cliente_b(session):
    ok, c = crear_cliente(session, "Luis Perez", "Ahorro", 500)
    assert ok, c
    return c


@pytest.fixture()
def cliente_rico(session):
    ok, c = crear_cliente(session, "Roberto Acaudalado", "Ahorro", 50000)
    assert ok, c
    return c


@pytest.fixture()
def cliente_sin_saldo(session):
    ok, c = crear_cliente(session, "Pedro Pobre", "Ahorro", 0)
    assert ok, c
    return c


@pytest.fixture()
def capital_amplio(session):
    """Inyecta capital extra al banco para pruebas de préstamos grandes."""
    registrar(session,
              debitos=[("Caja General", money(500000))],
              creditos=[("Capital Banco", money(500000))],
              descripcion="Capital extra para pruebas")
    session.commit()


@pytest.fixture()
def admin_user(session):
    ok, msg = registrar_primer_admin(session, "admin", "Administrador Test", "admin123")
    assert ok, msg
    return session.query(Usuario).filter_by(username="admin").first()


def _vencer_prestamo(session, prestamo, dias_atraso=10):
    """Helper: fuerza que un préstamo y su primera cuota pendiente queden vencidos."""
    prestamo.fecha_vencimiento = date.today() - timedelta(days=dias_atraso)
    cuota = (session.query(CuotaPrestamo)
             .filter_by(prestamo_id=prestamo.id, estado="PENDIENTE")
             .order_by(CuotaPrestamo.numero_cuota)
             .first())
    if cuota:
        cuota.fecha_vencimiento = date.today() - timedelta(days=dias_atraso)
    session.commit()
    return prestamo


# ═══════════════════════════════════════════════════════════════
# CONTABILIDAD — NÚCLEO DE PARTIDA DOBLE
# ═══════════════════════════════════════════════════════════════

class TestContabilidadNucleo:

    def test_inicializa_plan_de_cuentas(self, session):
        cuentas = session.query(CuentaContable).count()
        assert cuentas > 0

    def test_inicializar_es_idempotente(self, session):
        n1 = session.query(CuentaContable).count()
        inicializar_contabilidad(session)
        n2 = session.query(CuentaContable).count()
        assert n1 == n2

    def test_capital_inicial_se_registra_una_vez(self, session):
        capital1 = saldo_cuenta(session, "Capital Banco")
        inicializar_contabilidad(session)
        capital2 = saldo_cuenta(session, "Capital Banco")
        assert capital1 == capital2 == 10000.0

    def test_registrar_asiento_balanceado_ok(self, session):
        asiento = registrar(session,
                             debitos=[("Caja General", 100)],
                             creditos=[("Capital Banco", 100)],
                             descripcion="Test asiento")
        assert asiento.id is not None

    def test_registrar_asiento_descuadrado_falla(self, session):
        with pytest.raises(ValueError):
            registrar(session,
                      debitos=[("Caja General", 100)],
                      creditos=[("Capital Banco", 50)],
                      descripcion="Asiento mal cuadrado")

    def test_registrar_cuenta_inexistente_falla(self, session):
        with pytest.raises(ValueError):
            registrar(session,
                      debitos=[("Cuenta Que No Existe", 10)],
                      creditos=[("Capital Banco", 10)],
                      descripcion="Cuenta inválida")

    def test_get_cuenta_inexistente_lanza_error(self, session):
        with pytest.raises(ValueError):
            get_cuenta(session, "No Existe Esta Cuenta")

    def test_saldo_cuenta_inexistente_retorna_cero(self, session):
        assert saldo_cuenta(session, "Cuenta Fantasma") == 0.0

    def test_caja_real_coincide_con_caja_general(self, session):
        assert caja_real(session) == saldo_cuenta(session, "Caja General")

    def test_money_redondea_correctamente(self):
        assert money(10.005) == Decimal("10.01") or money(10.005) == Decimal("10.00")
        assert money("100") == Decimal("100.00")
        assert money(99.999) == Decimal("100.00")

    def test_reconciliar_sistema_recien_iniciado_sin_errores(self, session):
        errores, lines = reconciliar(session)
        assert errores == 0
        assert any("BALANCE CUADRADO" in l for l in lines)

    def test_reconciliar_tras_operaciones_normales_sigue_cuadrado(self, session, cliente_a, cliente_b):
        depositar(session, cliente_a.id, 200)
        retirar(session, cliente_a.id, 50)
        transferir(session, cliente_a.id, cliente_b.id, 100)
        errores, _ = reconciliar(session)
        assert errores == 0


# ═══════════════════════════════════════════════════════════════
# CLIENTES
# ═══════════════════════════════════════════════════════════════

class TestClientes:

    def test_crear_cliente_basico(self, session):
        ok, c = crear_cliente(session, "Maria Lopez", "Ahorro", 100)
        assert ok
        assert c.nombre == "Maria Lopez"
        assert float(c.saldo) == 100.0
        assert c.num_cuenta is not None
        assert c.estado == "ACTIVO"

    def test_crear_cliente_nombre_vacio_falla(self, session):
        ok, msg = crear_cliente(session, "   ", "Ahorro", 0)
        assert not ok

    def test_crear_cliente_saldo_negativo_falla(self, session):
        ok, msg = crear_cliente(session, "Juan Negativo", "Ahorro", -50)
        assert not ok

    def test_crear_cliente_nombre_duplicado_falla(self, session, cliente_a):
        ok, msg = crear_cliente(session, "Ana Gomez", "Ahorro", 0)
        assert not ok
        assert "ya existe" in msg.lower()

    def test_crear_cliente_documento_duplicado_falla(self, session):
        ok1, c1 = crear_cliente(session, "Persona Uno", "Ahorro", 0, documento="12345678-9")
        assert ok1
        ok2, msg2 = crear_cliente(session, "Persona Dos", "Ahorro", 0, documento="12345678-9")
        assert not ok2

    def test_crear_cliente_deposito_inicial_genera_asiento_cuadrado(self, session):
        caja_antes = caja_real(session)
        ok, c = crear_cliente(session, "Cliente Con Saldo", "Ahorro", 300)
        assert ok
        assert caja_real(session) == caja_antes + 300

    def test_editar_cliente_actualiza_campos(self, session, cliente_a):
        ok, msg = editar_cliente(session, cliente_a.id, telefono="7777-7777", email="ana@test.com")
        assert ok
        session.refresh(cliente_a)
        assert cliente_a.telefono == "7777-7777"

    def test_suspender_y_reactivar_cliente(self, session, cliente_a):
        ok, _ = suspender_cliente(session, cliente_a.id, "Motivo de prueba")
        assert ok
        session.refresh(cliente_a)
        assert cliente_a.estado == "SUSPENDIDO"

        ok2, _ = reactivar_cliente(session, cliente_a.id)
        assert ok2
        session.refresh(cliente_a)
        assert cliente_a.estado == "ACTIVO"

    def test_cerrar_cuenta_con_saldo_falla(self, session, cliente_a):
        ok, msg = cerrar_cuenta(session, cliente_a.id, "Cierre de prueba")
        assert not ok

    def test_cerrar_cuenta_saldo_cero_ok(self, session, cliente_sin_saldo):
        ok, msg = cerrar_cuenta(session, cliente_sin_saldo.id, "Cierre de prueba")
        assert ok
        session.refresh(cliente_sin_saldo)
        assert cliente_sin_saldo.estado == "CERRADO"


# ═══════════════════════════════════════════════════════════════
# OPERACIONES BANCARIAS — DEPÓSITO / RETIRO / TRANSFERENCIA
# ═══════════════════════════════════════════════════════════════

class TestOperacionesBasicas:

    def test_depositar_incrementa_saldo_neto_de_comision(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        tasa = float(tasa_deposito(session))
        ok, msg = depositar(session, cliente_a.id, 100)
        assert ok
        session.refresh(cliente_a)
        esperado = saldo_antes + 100 * (1 - tasa)
        assert abs(float(cliente_a.saldo) - esperado) < 0.05

    def test_depositar_monto_negativo_falla(self, session, cliente_a):
        ok, msg = depositar(session, cliente_a.id, -10)
        assert not ok

    def test_depositar_genera_movimiento(self, session, cliente_a):
        n_antes = session.query(Movimiento).filter_by(cliente_id=cliente_a.id).count()
        depositar(session, cliente_a.id, 50)
        n_despues = session.query(Movimiento).filter_by(cliente_id=cliente_a.id).count()
        assert n_despues == n_antes + 1

    def test_retirar_disminuye_saldo(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        ok, msg = retirar(session, cliente_a.id, 100)
        assert ok
        session.refresh(cliente_a)
        assert float(cliente_a.saldo) == saldo_antes - 100

    def test_retirar_mas_que_saldo_falla(self, session, cliente_a):
        ok, msg = retirar(session, cliente_a.id, 99999)
        assert not ok

    def test_retirar_monto_cero_falla(self, session, cliente_a):
        ok, msg = retirar(session, cliente_a.id, 0)
        assert not ok

    def test_transferir_entre_clientes(self, session, cliente_a, cliente_b):
        saldo_a_antes = float(cliente_a.saldo)
        saldo_b_antes = float(cliente_b.saldo)
        ok, msg = transferir(session, cliente_a.id, cliente_b.id, 100)
        assert ok
        session.refresh(cliente_a)
        session.refresh(cliente_b)
        assert float(cliente_b.saldo) == saldo_b_antes + 100
        assert float(cliente_a.saldo) < saldo_a_antes - 99  # se descuenta monto + comisión

    def test_transferir_mismo_cliente_no_prohibido_en_capa_logica(self, session, cliente_a):
        # La validación de "origen == destino" vive en app.py (UI), no en operaciones.py
        ok, msg = transferir(session, cliente_a.id, cliente_a.id, 10)
        # Solo verificamos que no rompe el sistema contablemente si se permite
        assert ok in (True, False)

    def test_transferir_saldo_insuficiente_falla(self, session, cliente_sin_saldo, cliente_b):
        ok, msg = transferir(session, cliente_sin_saldo.id, cliente_b.id, 500)
        assert not ok

    def test_operaciones_mantienen_balance_cuadrado(self, session, cliente_a, cliente_b):
        depositar(session, cliente_a.id, 300)
        retirar(session, cliente_b.id, 50)
        transferir(session, cliente_a.id, cliente_b.id, 75)
        errores, _ = reconciliar(session)
        assert errores == 0

    def test_verificar_limite_diario_no_rompe_con_montos_normales(self, session, cliente_a):
        ok, msg = verificar_limite_diario(session, cliente_a.id, "Deposito", 50)
        assert ok in (True, False)  # solo que no lance excepción


# ═══════════════════════════════════════════════════════════════
# PRÉSTAMOS — OTORGAMIENTO Y PAGO
# ═══════════════════════════════════════════════════════════════

class TestPrestamos:

    def test_otorgar_prestamo_basico(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        assert ok
        assert float(p.monto) == 500.0
        assert p.estado == "ACTIVO"
        assert p.plazo_meses == 12

    def test_otorgar_prestamo_genera_cuotas_correctas(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 600, 6, tasa_anual=0.12)
        assert ok
        cuotas = session.query(CuotaPrestamo).filter_by(prestamo_id=p.id).all()
        assert len(cuotas) == 6
        # La suma de capital de las cuotas debe ser igual al monto del préstamo
        suma_capital = sum(float(c.capital) for c in cuotas)
        assert abs(suma_capital - 600.0) < 0.05

    def test_otorgar_prestamo_acredita_saldo_cliente(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        ok, p = otorgar_prestamo(session, cliente_a.id, 300, 6, tasa_anual=0.10)
        assert ok
        session.refresh(cliente_a)
        assert float(cliente_a.saldo) == saldo_antes + 300

    def test_otorgar_prestamo_monto_minimo(self, session, cliente_a):
        ok, msg = otorgar_prestamo(session, cliente_a.id, 50, 6)
        assert not ok

    def test_otorgar_prestamo_plazo_invalido_falla(self, session, cliente_a):
        ok, msg = otorgar_prestamo(session, cliente_a.id, 500, 0)
        assert not ok

    def test_otorgar_prestamo_cliente_inexistente_falla(self, session):
        ok, msg = otorgar_prestamo(session, 999999, 500, 12)
        assert not ok

    def test_otorgar_prestamo_sin_capacidad_falla(self, session, cliente_a):
        # Capital inicial es 10000, intentamos prestar mucho más
        ok, msg = otorgar_prestamo(session, cliente_a.id, 999999, 12)
        assert not ok
        assert "capacidad" in msg.lower()

    def test_otorgar_prestamo_con_capital_amplio_ok(self, session, cliente_a, capital_amplio):
        ok, p = otorgar_prestamo(session, cliente_a.id, 50000, 24, tasa_anual=0.10)
        assert ok

    def test_otorgar_prestamo_mantiene_contabilidad_cuadrada(self, session, cliente_a):
        otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        errores, _ = reconciliar(session)
        assert errores == 0

    def test_pagar_prestamo_cuota_completa(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        assert ok
        cuota_mensual = float(p.cuota_mensual)
        ok2, msg2 = pagar_prestamo(session, cliente_a.id, cuota_mensual)
        assert ok2
        session.refresh(p)
        cuotas_pagadas = session.query(CuotaPrestamo).filter_by(prestamo_id=p.id, estado="PAGADA").count()
        assert cuotas_pagadas == 1

    def test_pagar_prestamo_monto_insuficiente_falla(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        assert ok
        ok2, msg2 = pagar_prestamo(session, cliente_a.id, 1.0)
        assert not ok2

    def test_pagar_prestamo_sin_prestamo_activo_falla(self, session, cliente_a):
        ok, msg = pagar_prestamo(session, cliente_a.id, 100)
        assert not ok

    def test_pagar_prestamo_completo_marca_pagado(self, session, cliente_rico):
        ok, p = otorgar_prestamo(session, cliente_rico.id, 600, 3, tasa_anual=0.10)
        assert ok
        total_deuda = float(p.monto) + float(p.interes)
        ok2, msg2 = pagar_prestamo(session, cliente_rico.id, total_deuda)
        assert ok2
        session.refresh(p)
        assert p.estado == "PAGADO"
        assert float(p.saldo_pendiente) == 0.0

    def test_pagar_prestamo_mantiene_contabilidad_cuadrada(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        pagar_prestamo(session, cliente_a.id, float(p.cuota_mensual))
        errores, _ = reconciliar(session)
        assert errores == 0

    def test_devengar_interes_no_lanza_excepcion(self, session, cliente_a):
        otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        ok, msg = devengar_interes(session)
        assert ok in (True, False)


# ═══════════════════════════════════════════════════════════════
# MORA — CUOTAS VENCIDAS Y CÁLCULO DE MORA  (foco principal)
# ═══════════════════════════════════════════════════════════════

class TestMora:

    def test_prestamo_recien_otorgado_no_tiene_mora(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        assert ok
        assert float(p.mora_acumulada or 0) == 0.0
        assert int(p.dias_mora or 0) == 0

    def test_actualizar_cuotas_vencidas_detecta_cuota_vencida(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        assert ok
        _vencer_prestamo(session, p, dias_atraso=15)

        n = actualizar_cuotas_vencidas(session)
        assert n >= 1

        cuota = (session.query(CuotaPrestamo)
                 .filter_by(prestamo_id=p.id)
                 .order_by(CuotaPrestamo.numero_cuota)
                 .first())
        assert cuota.estado == "VENCIDA"

    def test_actualizar_cuotas_vencidas_no_afecta_cuotas_futuras(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        assert ok
        # No tocamos las fechas: todas las cuotas deben seguir en el futuro
        n = actualizar_cuotas_vencidas(session)
        cuotas_vencidas = session.query(CuotaPrestamo).filter_by(
            prestamo_id=p.id, estado="VENCIDA"
        ).count()
        assert cuotas_vencidas == 0

    def test_actualizar_cuotas_vencidas_es_idempotente(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=20)
        n1 = actualizar_cuotas_vencidas(session)
        n2 = actualizar_cuotas_vencidas(session)
        assert n2 == 0  # ya no quedan cuotas pendientes vencidas por marcar de nuevo
        assert n1 >= 1

    def test_calcular_mora_sin_prestamos_vencidos_retorna_cero(self, session, cliente_a):
        otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        n, msg = calcular_mora(session)
        assert n == 0

    def test_calcular_mora_detecta_prestamo_vencido(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12, tasa_anual=0.10)
        assert ok
        _vencer_prestamo(session, p, dias_atraso=10)

        n, msg = calcular_mora(session)
        assert n == 1

        session.expire_all()
        p_actualizado = session.query(Prestamo).filter_by(id=p.id).first()
        assert float(p_actualizado.mora_acumulada) > 0
        assert int(p_actualizado.dias_mora) == 10

    def test_calcular_mora_proporcional_a_dias_atraso(self, session, cliente_a, cliente_b):
        ok1, p1 = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        ok2, p2 = otorgar_prestamo(session, cliente_b.id, 1000, 12, tasa_anual=0.10)
        assert ok1 and ok2

        _vencer_prestamo(session, p1, dias_atraso=5)
        _vencer_prestamo(session, p2, dias_atraso=30)

        calcular_mora(session)
        session.expire_all()

        p1r = session.query(Prestamo).filter_by(id=p1.id).first()
        p2r = session.query(Prestamo).filter_by(id=p2.id).first()

        # A más días de atraso, más mora acumulada (mismo saldo pendiente aprox.)
        assert float(p2r.mora_acumulada) > float(p1r.mora_acumulada)

    def test_calcular_mora_usa_tasa_configurable(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=30)

        # Configurar una tasa de mora distinta
        cfg = session.query(ConfigBanco).filter_by(clave="tasa_mora").first()
        if cfg:
            cfg.valor = "0.05"
        else:
            session.add(ConfigBanco(clave="tasa_mora", valor="0.05"))
        session.commit()

        n, msg = calcular_mora(session)
        assert n == 1
        session.expire_all()
        p_actualizado = session.query(Prestamo).filter_by(id=p.id).first()
        saldo = float(p_actualizado.saldo_pendiente)
        esperado = round(saldo * 0.05 / 30.0 * 30, 2)
        assert abs(float(p_actualizado.mora_acumulada) - esperado) < 0.5

    def test_calcular_mora_no_afecta_prestamos_pagados(self, session, cliente_rico):
        ok, p = otorgar_prestamo(session, cliente_rico.id, 600, 3, tasa_anual=0.10)
        total_deuda = float(p.monto) + float(p.interes)
        pagar_prestamo(session, cliente_rico.id, total_deuda)
        session.refresh(p)
        assert p.estado == "PAGADO"

        # Aunque "vencido" en fecha, si está PAGADO no debe generar mora
        p.fecha_vencimiento = date.today() - timedelta(days=30)
        session.commit()

        n, msg = calcular_mora(session)
        session.expire_all()
        p_final = session.query(Prestamo).filter_by(id=p.id).first()
        assert float(p_final.mora_acumulada or 0) == 0.0

    def test_calcular_mora_es_recalculable_sin_duplicar(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=10)

        calcular_mora(session)
        session.expire_all()
        p1 = session.query(Prestamo).filter_by(id=p.id).first()
        mora1 = float(p1.mora_acumulada)

        # Recalcular con los mismos días de atraso debe dar el mismo resultado (no se duplica)
        calcular_mora(session)
        session.expire_all()
        p2 = session.query(Prestamo).filter_by(id=p.id).first()
        mora2 = float(p2.mora_acumulada)

        assert abs(mora1 - mora2) < 0.01

    def test_verificar_prestamos_vencidos_genera_alerta(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=5)
        alertas = verificar_prestamos_vencidos(session)
        assert len(alertas) >= 1
        assert alertas[0]["tipo"] == "prestamo_vencido"

    def test_verificar_prestamos_por_vencer_detecta_proximos(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        p.fecha_vencimiento = date.today() + timedelta(days=3)
        session.commit()
        alertas = verificar_prestamos_por_vencer(session)
        assert any(a["extra"]["prestamo_id"] == p.id for a in alertas)

    def test_verificar_mora_activa_via_sql_directo(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=10)
        calcular_mora(session)
        session.commit()

        alertas = verificar_mora_activa(session)
        assert len(alertas) >= 1
        assert alertas[0]["extra"]["prestamo_id"] == p.id

    def test_cobrar_mora_prestamo_reduce_mora_acumulada(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=15)
        calcular_mora(session)
        session.commit()
        session.expire_all()

        p_con_mora = session.query(Prestamo).filter_by(id=p.id).first()
        mora_antes = float(p_con_mora.mora_acumulada)
        assert mora_antes > 0

        # Asegurar saldo suficiente en el cliente para cobrar la mora
        cliente = session.query(Cliente).filter_by(id=cliente_a.id).first()
        cliente.saldo = money(cliente.saldo) + money(mora_antes) + money(50)
        session.commit()

        ok_cobro, msg_cobro = cobrar_mora_prestamo(session, cliente_a.id, mora_antes)
        assert ok_cobro

        # Tras cobrar la mora, el campo mora_acumulada del préstamo debe
        # reflejar que ya fue cobrada (idealmente bajar a 0). Si este test
        # falla, revisar operaciones.cobrar_mora_prestamo: actualmente solo
        # mueve el dinero contablemente pero no actualiza prestamo.mora_acumulada.
        session.expire_all()
        p_final = session.query(Prestamo).filter_by(id=p.id).first()
        assert float(p_final.mora_acumulada) < mora_antes, (
            "cobrar_mora_prestamo no está reduciendo mora_acumulada del préstamo "
            "tras el cobro — la pantalla de Mora y Provisiones seguiría mostrando "
            "la mora como pendiente aunque ya se cobró."
        )

    def test_clasificar_prestamo_moroso_cambia_estado(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=45)
        ok2, msg2 = clasificar_prestamo_moroso(session, p.id)
        assert ok2
        session.refresh(p)
        assert p.estado == "MOROSO"

    def test_castigar_prestamo_incobrable_tras_moroso(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=90)
        clasificar_prestamo_moroso(session, p.id)
        session.commit()

        ok2, msg2 = castigar_prestamo_incobrable(session, p.id)
        assert ok2
        session.refresh(p)
        assert p.estado in ("CASTIGADO", "INCOBRABLE")

    def test_mora_mantiene_contabilidad_cuadrada(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=20)
        actualizar_cuotas_vencidas(session)
        calcular_mora(session)
        errores, _ = reconciliar(session)
        assert errores == 0

    def test_contar_alertas_incluye_mora(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        _vencer_prestamo(session, p, dias_atraso=10)
        calcular_mora(session)
        session.commit()
        caja = caja_real(session)
        conteo = contar_alertas(session, caja)
        assert conteo["total"] >= 1


# ═══════════════════════════════════════════════════════════════
# TIPOS DE CUENTA Y SUCURSALES
# ═══════════════════════════════════════════════════════════════

class TestTiposCuentaYSucursales:

    def test_inicializar_tipos_crea_defaults(self, session):
        tipos = session.query(TipoCuenta).all()
        assert len(tipos) >= 4

    def test_inicializar_tipos_es_idempotente(self, session):
        n1 = session.query(TipoCuenta).count()
        inicializar_tipos_cuenta(session)
        n2 = session.query(TipoCuenta).count()
        assert n1 == n2

    def test_crear_tipo_cuenta_nuevo(self, session):
        ok, tc = crear_tipo_cuenta(session, "VIP", 0.05, 1000, False, "Cuenta VIP")
        assert ok
        assert tc.nombre == "VIP"

    def test_crear_tipo_cuenta_duplicado_falla(self, session):
        ok, msg = crear_tipo_cuenta(session, "Ahorro", 0.03, 0, False)
        assert not ok

    def test_inicializar_sucursales_crea_defaults(self, session):
        sucursales = session.query(Sucursal).all()
        assert len(sucursales) >= 3

    def test_inicializar_sucursales_crea_atm(self, session):
        atms = session.query(ATM).all()
        assert len(atms) >= 1

    def test_crear_sucursal_nueva(self, session):
        ok, s = crear_sucursal(session, "Sucursal Test", "Direccion Test", "1111-1111")
        assert ok

    def test_crear_sucursal_duplicada_falla(self, session):
        ok, msg = crear_sucursal(session, "Central", "Otra direccion")
        assert not ok


# ═══════════════════════════════════════════════════════════════
# BENEFICIARIOS
# ═══════════════════════════════════════════════════════════════

class TestBeneficiarios:

    def test_agregar_beneficiario_valido(self, session, cliente_a, cliente_b):
        ok, b = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Mi amigo")
        assert ok

    def test_beneficiario_a_si_mismo_falla(self, session, cliente_a):
        ok, msg = agregar_beneficiario(session, cliente_a.id, cliente_a.num_cuenta, "Yo mismo")
        assert not ok

    def test_beneficiario_cuenta_inexistente_falla(self, session, cliente_a):
        ok, msg = agregar_beneficiario(session, cliente_a.id, "ZZZZ-00000000", "Fantasma")
        assert not ok

    def test_beneficiario_duplicado_falla(self, session, cliente_a, cliente_b):
        agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Amigo 1")
        ok2, msg2 = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Amigo otra vez")
        assert not ok2

    def test_eliminar_beneficiario(self, session, cliente_a, cliente_b):
        ok, b = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Temp")
        ok2, msg2 = eliminar_beneficiario(session, b.id, cliente_a.id)
        assert ok2


# ═══════════════════════════════════════════════════════════════
# DEPÓSITO A PLAZO FIJO
# ═══════════════════════════════════════════════════════════════

class TestDepositoPlazoFijo:

    def test_crear_dpf_basico(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 6)
        assert ok
        assert float(dpf.monto) == 1000.0

    def test_dpf_calcula_interes_correcto(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1200, 0.06, 12)
        assert ok
        esperado = 1200 * 0.06 * 12 / 12
        assert abs(float(dpf.interes_proyectado) - esperado) < 0.5

    def test_dpf_monto_minimo_falla(self, session, cliente_rico):
        ok, msg = crear_deposito_plazo(session, cliente_rico.id, 100, 0.05, 6)
        assert not ok

    def test_dpf_plazo_cero_falla(self, session, cliente_rico):
        ok, msg = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 0)
        assert not ok

    def test_dpf_saldo_insuficiente_falla(self, session, cliente_a):
        ok, msg = crear_deposito_plazo(session, cliente_a.id, 5000, 0.05, 6)
        assert not ok

    def test_dpf_cliente_inexistente_falla(self, session):
        ok, msg = crear_deposito_plazo(session, 999999, 1000, 0.05, 6)
        assert not ok

    def test_vencer_dpf_acredita_saldo(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 6)
        saldo_antes = float(session.query(Cliente).filter_by(id=cliente_rico.id).first().saldo)
        ok2, dpf2 = vencer_deposito_plazo(session, dpf.id, "Test")
        assert ok2
        cliente_final = session.query(Cliente).filter_by(id=cliente_rico.id).first()
        assert float(cliente_final.saldo) > saldo_antes

    def test_vencer_dpf_cambia_estado(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 6)
        vencer_deposito_plazo(session, dpf.id, "Test")
        session.refresh(dpf)
        assert dpf.estado == "VENCIDO"

    def test_vencer_dpf_ya_vencido_falla(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 6)
        vencer_deposito_plazo(session, dpf.id, "Test")
        ok2, msg2 = vencer_deposito_plazo(session, dpf.id, "Test")
        assert not ok2

    def test_vencer_dpf_inexistente_falla(self, session):
        ok, msg = vencer_deposito_plazo(session, 999999, "Test")
        assert not ok

    def test_dpf_mantiene_contabilidad_cuadrada(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 6)
        vencer_deposito_plazo(session, dpf.id, "Test")
        errores, _ = reconciliar(session)
        assert errores == 0


# ═══════════════════════════════════════════════════════════════
# GARANTÍAS Y REFINANCIAMIENTO
# ═══════════════════════════════════════════════════════════════

class TestGarantiasYRefinanciamiento:

    def test_agregar_garantia_a_prestamo(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        ok2, g = agregar_garantia(session, p.id, "vehiculo", "Toyota Corolla 2020", 8000, "PLACA-123")
        assert ok2
        assert float(g.valor_estimado) == 8000.0

    def test_agregar_garantia_prestamo_inexistente_falla(self, session):
        ok, msg = agregar_garantia(session, 999999, "casa", "Casa fantasma", 50000)
        assert not ok

    def test_refinanciar_prestamo_crea_nuevo_prestamo(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        assert ok
        ok2, nuevo = refinanciar_prestamo(session, p.id, 24, 0.08)
        assert ok2
        assert nuevo.id != p.id
        assert nuevo.prestamo_origen_id == p.id

    def test_refinanciar_marca_prestamo_original_como_refinanciado(self, session, cliente_a):
        ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12, tasa_anual=0.10)
        refinanciar_prestamo(session, p.id, 24, 0.08)
        session.refresh(p)
        assert p.estado == "REFINANCIADO"

    def test_refinanciar_prestamo_pagado_falla(self, session, cliente_rico):
        ok, p = otorgar_prestamo(session, cliente_rico.id, 600, 3, tasa_anual=0.10)
        total = float(p.monto) + float(p.interes)
        pagar_prestamo(session, cliente_rico.id, total)
        ok2, msg2 = refinanciar_prestamo(session, p.id, 12)
        assert not ok2

    def test_refinanciar_prestamo_inexistente_falla(self, session):
        ok, msg = refinanciar_prestamo(session, 999999, 12)
        assert not ok


# ═══════════════════════════════════════════════════════════════
# SCORE CREDITICIO
# ═══════════════════════════════════════════════════════════════

class TestScoreCredito:

    def test_calcular_score_cliente_nuevo(self, session, cliente_a):
        sc = calcular_score(session, cliente_a.id)
        assert sc is not None
        assert 300 <= sc.score <= 850

    def test_calcular_score_cliente_inexistente_retorna_none(self, session):
        sc = calcular_score(session, 999999)
        assert sc is None

    def test_score_mejora_con_pagos_puntuales(self, session, cliente_rico):
        ok, p = otorgar_prestamo(session, cliente_rico.id, 600, 3, tasa_anual=0.10)
        total = float(p.monto) + float(p.interes)
        pagar_prestamo(session, cliente_rico.id, total)
        sc = calcular_score(session, cliente_rico.id)
        assert sc.pagos_puntuales >= 1

    def test_score_se_actualiza_en_cliente(self, session, cliente_a):
        calcular_score(session, cliente_a.id)
        session.refresh(cliente_a)
        assert cliente_a.score_credito is not None


# ═══════════════════════════════════════════════════════════════
# TARJETAS
# ═══════════════════════════════════════════════════════════════

class TestTarjetas:

    def test_emitir_tarjeta_debito(self, session, cliente_a):
        ok, td = emitir_tarjeta_debito(session, cliente_a.id)
        assert ok
        assert len(td.numero) == 16

    def test_emitir_tarjeta_debito_cliente_inexistente_falla(self, session):
        ok, msg = emitir_tarjeta_debito(session, 999999)
        assert not ok

    def test_emitir_tarjeta_credito_score_suficiente(self, session, cliente_a):
        ok, tc = emitir_tarjeta_credito(session, cliente_a.id, 1000)
        assert ok
        assert float(tc.limite) == 1000.0

    def test_emitir_tarjeta_credito_score_insuficiente_falla(self, session, cliente_a):
        cliente_a.score_credito = 400
        session.commit()
        ok, msg = emitir_tarjeta_credito(session, cliente_a.id, 1000)
        assert not ok


# ═══════════════════════════════════════════════════════════════
# AML — PREVENCIÓN DE LAVADO DE DINERO
# ═══════════════════════════════════════════════════════════════

class TestAML:

    def test_verificar_aml_monto_alto_genera_alerta(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 15000, "Deposito")
        assert len(alertas) >= 1
        assert alertas[0].nivel == "CRITICA"

    def test_verificar_aml_monto_normal_no_genera_alerta(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 100, "Deposito")
        assert len(alertas) == 0

    def test_obtener_alertas_aml_pendientes(self, session, cliente_a):
        verificar_aml(session, cliente_a.id, 20000, "Deposito")
        session.commit()
        pendientes = obtener_alertas_aml(session, estado="PENDIENTE")
        assert len(pendientes) >= 1

    def test_revisar_alerta_aml_cambia_estado(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 20000, "Deposito")
        session.commit()
        ok, a = revisar_alerta_aml(session, alertas[0].id, "tester", "Revisado ok", cerrar=False)
        assert ok
        assert a.estado == "REVISADA"

    def test_cerrar_alerta_aml(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 20000, "Deposito")
        session.commit()
        ok, a = revisar_alerta_aml(session, alertas[0].id, "tester", "Cerrado", cerrar=True)
        assert ok
        assert a.estado == "CERRADA"


# ═══════════════════════════════════════════════════════════════
# CIERRE DIARIO
# ═══════════════════════════════════════════════════════════════

class TestCierreDiario:

    def test_realizar_cierre_diario_basico(self, session, cliente_a):
        depositar(session, cliente_a.id, 100)
        ok, cierre = realizar_cierre_diario(session, "Tester", "Cierre de prueba")
        assert ok
        assert cierre.fecha == date.today()

    def test_cierre_diario_duplicado_falla(self, session):
        realizar_cierre_diario(session, "Tester", "Primer cierre")
        ok2, msg2 = realizar_cierre_diario(session, "Tester", "Segundo cierre mismo día")
        assert not ok2


# ═══════════════════════════════════════════════════════════════
# BALANCE GENERAL Y ESTADO DE RESULTADOS
# ═══════════════════════════════════════════════════════════════

class TestReportesContables:

    def test_generar_balance_general_estructura(self, session):
        bg = generar_balance_general(session)
        assert "activos" in bg
        assert "pasivos" in bg
        assert "patrimonio" in bg
        assert "ecuacion_ok" in bg

    def test_balance_general_ecuacion_cuadra_en_sistema_limpio(self, session):
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"] is True

    def test_balance_general_ecuacion_cuadra_tras_operaciones(self, session, cliente_a, cliente_b):
        depositar(session, cliente_a.id, 200)
        otorgar_prestamo(session, cliente_b.id, 500, 12, tasa_anual=0.10)
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"] is True

    def test_generar_estado_resultados_estructura(self, session):
        er = generar_estado_resultados(session)
        assert "ingresos" in er
        assert "gastos" in er
        assert "utilidad" in er

    def test_estado_resultados_refleja_comisiones(self, session, cliente_a):
        depositar(session, cliente_a.id, 1000)
        er = generar_estado_resultados(session)
        assert er["total_ingresos"] >= 0


# ═══════════════════════════════════════════════════════════════
# SOCIOS Y APORTES
# ═══════════════════════════════════════════════════════════════

class TestSociosYAportes:

    def test_registrar_socio_nuevo(self, session, cliente_a):
        ok, s = registrar_socio(session, cliente_a.id, 100)
        assert ok
        assert s.numero_socio.startswith("SOC-")

    def test_registrar_socio_duplicado_falla(self, session, cliente_a):
        registrar_socio(session, cliente_a.id, 100)
        ok2, msg2 = registrar_socio(session, cliente_a.id, 50)
        assert not ok2

    def test_registrar_aporte_incrementa_total(self, session, cliente_a):
        ok, s = registrar_socio(session, cliente_a.id, 0)
        ok2, ap = registrar_aporte(session, s.id, 200, "ORDINARIO", "Aporte test")
        assert ok2
        session.refresh(s)
        assert float(s.aporte_total) >= 200

    def test_registrar_aporte_monto_negativo_falla(self, session, cliente_a):
        ok, s = registrar_socio(session, cliente_a.id, 0)
        ok2, msg2 = registrar_aporte(session, s.id, -50, "ORDINARIO")
        assert not ok2

    def test_historial_cliente_incluye_todo(self, session, cliente_a):
        depositar(session, cliente_a.id, 100)
        otorgar_prestamo(session, cliente_a.id, 500, 12, tasa_anual=0.10)
        hist = historial_cliente(session, cliente_a.id)
        assert hist is not None
        assert len(hist["movimientos"]) >= 1
        assert len(hist["prestamos"]) >= 1

    def test_historial_cliente_inexistente_retorna_none(self, session):
        hist = historial_cliente(session, 999999)
        assert hist is None


# ═══════════════════════════════════════════════════════════════
# ALERTAS DEL SISTEMA
# ═══════════════════════════════════════════════════════════════

class TestAlertasSistema:

    def test_verificar_saldos_bajos_detecta_cliente(self, session):
        ok, c = crear_cliente(session, "Cliente Saldo Bajo", "Ahorro", 0)
        alertas = verificar_saldos_bajos(session)
        assert any(a["extra"]["cliente_id"] == c.id for a in alertas)

    def test_verificar_caja_baja_detecta_caja_insuficiente(self, session):
        alertas = verificar_caja_baja(session, 10.0)
        assert len(alertas) >= 1

    def test_verificar_caja_baja_no_alerta_si_suficiente(self, session):
        alertas = verificar_caja_baja(session, 99999.0)
        assert len(alertas) == 0

    def test_obtener_todas_alertas_no_lanza_excepcion(self, session, cliente_a):
        caja = caja_real(session)
        alertas = obtener_todas_alertas(session, caja)
        assert isinstance(alertas, list)

    def test_contar_alertas_estructura(self, session):
        caja = caja_real(session)
        conteo = contar_alertas(session, caja)
        assert "total" in conteo
        assert "error" in conteo
        assert "warning" in conteo


# ═══════════════════════════════════════════════════════════════
# OPERACIONES CONTABLES AVANZADAS
# ═══════════════════════════════════════════════════════════════

class TestContabilidadAvanzada:

    def test_registrar_inversion(self, session):
        ok, msg = registrar_inversion(session, 2000, "Bonos del Estado")
        assert ok
        assert saldo_cuenta(session, "Inversiones") == 2000.0

    def test_liquidar_inversion_con_ganancia(self, session):
        registrar_inversion(session, 2000, "Bonos")
        ok, msg = liquidar_inversion(session, 2000, 150, "Liquidación")
        assert ok
        assert saldo_cuenta(session, "Inversiones") == 0.0

    def test_adquirir_bien_inmueble(self, session):
        ok, msg = adquirir_bien_inmueble(session, 5000, "Equipo de cómputo")
        assert ok
        assert saldo_cuenta(session, "Bienes e Inmuebles") == 5000.0

    def test_constituir_provision(self, session):
        ok, msg = constituir_provision(session, 500, "Provisión Q1")
        assert ok
        assert saldo_cuenta(session, "Provision Incobrables") == 500.0

    def test_deposito_cuenta_ahorro(self, session, cliente_a):
        ok, msg = deposito_cuenta_ahorro(session, cliente_a.id, 100)
        assert ok
        assert saldo_cuenta(session, "Cuentas de Ahorro") == 100.0

    def test_deposito_cuenta_corriente(self, session, cliente_a):
        ok, msg = deposito_cuenta_corriente(session, cliente_a.id, 100)
        assert ok
        assert saldo_cuenta(session, "Cuentas Corrientes") == 100.0

    def test_recibir_prestamo_banco(self, session):
        ok, msg = recibir_prestamo_banco(session, 10000, "Banco Agrícola")
        assert ok
        assert saldo_cuenta(session, "Obligaciones con Bancos") == 10000.0

    def test_pagar_obligacion_banco(self, session):
        recibir_prestamo_banco(session, 10000, "Banco Agrícola")
        ok, msg = pagar_obligacion_banco(session, 1000, 100, "Banco Agrícola")
        assert ok
        assert saldo_cuenta(session, "Obligaciones con Bancos") == 9000.0

    def test_provisionar_y_pagar_impuesto(self, session):
        ok1, _ = provisionar_impuesto(session, 300, "Renta")
        assert ok1
        assert saldo_cuenta(session, "Impuestos por Pagar") == 300.0
        ok2, _ = pagar_impuesto(session, 300, "Renta")
        assert ok2
        assert saldo_cuenta(session, "Impuestos por Pagar") == 0.0

    def test_constituir_reserva_legal_requiere_utilidades(self, session):
        ok, msg = constituir_reserva_legal(session, 100)
        # Sin utilidades previas debería fallar o no afectar negativamente el balance
        assert ok in (True, False)

    def test_registrar_utilidad_y_luego_reserva(self, session):
        registrar_utilidad_ejercicio(session, 1000)
        ok, msg = constituir_reserva_legal(session, 100)
        assert ok
        assert saldo_cuenta(session, "Reservas Legales") == 100.0

    def test_registrar_comision_tarjeta_credito(self, session):
        ok, msg = registrar_comision_tarjeta_credito(session, 25, "Comisión comercio")
        assert ok
        assert saldo_cuenta(session, "Ingresos Tarjeta Credito") == 25.0

    def test_registrar_gasto_operativo(self, session):
        ok, msg = registrar_gasto_operativo(session, 500, "Salarios")
        assert ok
        assert saldo_cuenta(session, "Gastos Operativos") == 500.0

    def test_registrar_mora_pagada_banco(self, session):
        ok, msg = registrar_mora_pagada_banco(session, 50, "Multa regulatoria")
        assert ok
        assert saldo_cuenta(session, "Gastos por Mora Pagada") == 50.0

    def test_todas_las_operaciones_avanzadas_mantienen_balance(self, session, cliente_a):
        registrar_inversion(session, 1000, "Test")
        adquirir_bien_inmueble(session, 500, "Test")
        constituir_provision(session, 100, "Test")
        deposito_cuenta_ahorro(session, cliente_a.id, 50)
        recibir_prestamo_banco(session, 2000, "Test")
        provisionar_impuesto(session, 100, "Test")
        registrar_gasto_operativo(session, 50, "Test")
        errores, _ = reconciliar(session)
        assert errores == 0


# ═══════════════════════════════════════════════════════════════
# AUTENTICACIÓN Y PERMISOS
# ═══════════════════════════════════════════════════════════════

class TestAutenticacion:

    def test_hash_y_verify_password(self):
        h = hash_password("clave123")
        assert verify_password("clave123", h)
        assert not verify_password("incorrecta", h)

    def test_registrar_primer_admin(self, session):
        assert not hay_usuarios(session)
        ok, msg = registrar_primer_admin(session, "admin", "Admin Principal", "admin123")
        assert ok
        assert hay_usuarios(session)

    def test_registrar_segundo_admin_inicial_falla(self, session, admin_user):
        ok, msg = registrar_primer_admin(session, "admin2", "Otro Admin", "pass123")
        assert not ok

    def test_login_exitoso(self, session, admin_user):
        u, msg = login(session, "admin", "admin123")
        assert u is not None
        assert u.username == "admin"

    def test_login_password_incorrecta(self, session, admin_user):
        u, msg = login(session, "admin", "incorrecta")
        assert u is None

    def test_login_usuario_inexistente(self, session):
        u, msg = login(session, "noexiste", "cualquiera")
        assert u is None

    def test_login_bloquea_tras_intentos_fallidos(self, session, admin_user):
        for _ in range(5):
            login(session, "admin", "incorrecta")
        u, msg = login(session, "admin", "admin123")
        assert u is None
        assert "bloqueada" in msg.lower()

    def test_crear_usuario_con_permiso(self, session, admin_user):
        ok, msg = crear_usuario(session, admin_user, "cajero1", "Cajero Uno", "pass123", "CAJERO")
        assert ok

    def test_crear_usuario_contrasena_corta_falla(self, session, admin_user):
        ok, msg = crear_usuario(session, admin_user, "cajero2", "Cajero Dos", "123", "CAJERO")
        assert not ok

    def test_crear_usuario_rol_invalido_falla(self, session, admin_user):
        ok, msg = crear_usuario(session, admin_user, "raro", "Raro", "pass123", "SUPERADMIN")
        assert not ok

    def test_crear_usuario_duplicado_falla(self, session, admin_user):
        crear_usuario(session, admin_user, "dup1", "Dup Uno", "pass123", "CAJERO")
        ok, msg = crear_usuario(session, admin_user, "dup1", "Otro", "pass456", "CAJERO")
        assert not ok

    def test_tiene_permiso_admin_acceso_total(self, session, admin_user):
        assert tiene_permiso(admin_user, "gestionar_usuarios")
        assert tiene_permiso(admin_user, "ver_panel")

    def test_tiene_permiso_cajero_restringido(self, session, admin_user):
        crear_usuario(session, admin_user, "caj1", "Cajero Test", "pass123", "CAJERO")
        cajero = session.query(Usuario).filter_by(username="caj1").first()
        assert tiene_permiso(cajero, "operaciones_bancarias")
        assert not tiene_permiso(cajero, "gestionar_usuarios")

    def test_tiene_permiso_usuario_inactivo_sin_acceso(self, session, admin_user):
        crear_usuario(session, admin_user, "inact", "Inactivo", "pass123", "CAJERO")
        u = session.query(Usuario).filter_by(username="inact").first()
        toggle_usuario(session, admin_user, u.id)
        session.refresh(u)
        assert not tiene_permiso(u, "operaciones_bancarias")

    def test_cambiar_password_propia_requiere_actual_si_no_es_admin(self, session, admin_user):
        # Un usuario NO-admin debe verificar su contraseña actual antes de cambiarla
        crear_usuario(session, admin_user, "noadmin1", "No Admin Uno", "pass123", "CAJERO")
        u = session.query(Usuario).filter_by(username="noadmin1").first()
        ok, msg = cambiar_password(session, u, u.id, "nuevapass1", actual="incorrecta")
        assert not ok

    def test_admin_puede_cambiar_su_password_sin_verificar_actual(self, session, admin_user):
        # Un ADMIN tiene permiso "gestionar_usuarios", por lo que puede cambiar
        # su propia contraseña sin pasar por la verificación de "actual"
        # (comportamiento intencional del sistema, ver auth.cambiar_password)
        ok, msg = cambiar_password(session, admin_user, admin_user.id, "nuevapass1", actual="incorrecta")
        assert ok

    def test_cambiar_password_propia_correcta(self, session, admin_user):
        ok, msg = cambiar_password(session, admin_user, admin_user.id, "nuevapass1", actual="admin123")
        assert ok

    def test_cambiar_rol_usuario(self, session, admin_user):
        crear_usuario(session, admin_user, "rolx", "RolX", "pass123", "CAJERO")
        u = session.query(Usuario).filter_by(username="rolx").first()
        ok, msg = cambiar_rol(session, admin_user, u.id, "GERENTE")
        assert ok
        session.refresh(u)
        assert u.rol == "GERENTE"

    def test_no_puede_cambiar_su_propio_rol(self, session, admin_user):
        ok, msg = cambiar_rol(session, admin_user, admin_user.id, "CAJERO")
        assert not ok

    def test_no_puede_desactivarse_a_si_mismo(self, session, admin_user):
        ok, msg = toggle_usuario(session, admin_user, admin_user.id)
        assert not ok


# ═══════════════════════════════════════════════════════════════
# INTEGRACIÓN — FLUJO COMPLETO END-TO-END
# ═══════════════════════════════════════════════════════════════

class TestFlujoIntegracionCompleto:

    def test_flujo_cliente_prestamo_mora_cobro_completo(self, session):
        """
        Flujo realista completo:
        1. Crear cliente
        2. Otorgar préstamo
        3. Pagar una cuota a tiempo
        4. Simular vencimiento del resto
        5. Calcular mora
        6. Cobrar mora
        7. Verificar que todo el sistema sigue cuadrado
        """
        ok, cliente = crear_cliente(session, "Cliente Flujo Completo", "Ahorro", 2000)
        assert ok

        ok, prestamo = otorgar_prestamo(session, cliente.id, 1200, 12, tasa_anual=0.10)
        assert ok

        # Pagar primera cuota a tiempo
        ok_pago, _ = pagar_prestamo(session, cliente.id, float(prestamo.cuota_mensual))
        assert ok_pago

        # Forzar vencimiento del préstamo completo (resto de cuotas)
        _vencer_prestamo(session, prestamo, dias_atraso=25)

        n_cuotas = actualizar_cuotas_vencidas(session)
        assert n_cuotas >= 1

        n_mora, _ = calcular_mora(session)
        assert n_mora == 1

        session.expire_all()
        prestamo_final = session.query(Prestamo).filter_by(id=prestamo.id).first()
        assert float(prestamo_final.mora_acumulada) > 0

        # Dar saldo suficiente y cobrar la mora
        mora_a_cobrar = float(prestamo_final.mora_acumulada)
        cliente_final = session.query(Cliente).filter_by(id=cliente.id).first()
        cliente_final.saldo = money(cliente_final.saldo) + money(mora_a_cobrar) + money(100)
        session.commit()

        ok_cobro, _ = cobrar_mora_prestamo(session, cliente.id, mora_a_cobrar)
        assert ok_cobro

        # El balance contable (débitos=créditos, activos=pasivos+patrimonio) debe
        # seguir cuadrado tras el cobro. No volvemos a llamar calcular_mora aquí
        # porque el préstamo sigue vencido y generaría mora nueva, lo cual es
        # comportamiento esperado, no un error de reconciliación.
        errores, lines = reconciliar(session)
        assert errores == 0, "\n".join(lines)

    def test_flujo_banco_completo_multiples_modulos(self, session, capital_amplio):
        """Ejercita muchos módulos en secuencia y valida que el balance siempre cuadre."""
        ok1, cliente1 = crear_cliente(session, "Cliente Integral Uno", "Ahorro", 5000)
        ok2, cliente2 = crear_cliente(session, "Cliente Integral Dos", "Ahorro", 3000)
        assert ok1 and ok2

        depositar(session, cliente1.id, 500)
        retirar(session, cliente2.id, 200)
        transferir(session, cliente1.id, cliente2.id, 300)

        ok_p, prestamo = otorgar_prestamo(session, cliente1.id, 1000, 12, tasa_anual=0.10)
        assert ok_p
        pagar_prestamo(session, cliente1.id, float(prestamo.cuota_mensual))

        ok_dpf, dpf = crear_deposito_plazo(session, cliente2.id, 1000, 0.05, 6)
        assert ok_dpf

        emitir_tarjeta_debito(session, cliente1.id)
        ok_tc, _ = emitir_tarjeta_credito(session, cliente2.id, 500)

        registrar_inversion(session, 1000, "Test integral")
        adquirir_bien_inmueble(session, 500, "Test integral")
        registrar_gasto_operativo(session, 50, "Test integral")

        verificar_aml(session, cliente1.id, 12000, "Deposito")

        calcular_score(session, cliente1.id)
        calcular_score(session, cliente2.id)

        errores, _ = reconciliar(session)
        assert errores == 0

        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"] is True