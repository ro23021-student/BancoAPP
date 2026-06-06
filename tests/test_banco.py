"""
test_banco_completo.py — Suite de tests COMPLEMENTARIA para BancoAppSistemas
=============================================================================
Cubre todos los módulos y funciones NO cubiertas por test_banco.py:

  1.  extras.py — TipoCuenta, Sucursales, Beneficiarios
  2.  extras.py — Depósito a Plazo Fijo (crear + vencer)
  3.  extras.py — Garantías y Refinanciamiento
  4.  extras.py — Score Crediticio
  5.  extras.py — Tarjetas (débito y crédito)
  6.  extras.py — AML (Anti-Lavado de Dinero)
  7.  extras.py — Cierre Diario
  8.  extras.py — Balance General y Estado de Resultados
  9.  extras.py — Socios y Aportes
  10. extras.py — Historial Cliente y Cuotas Vencidas
  11. operaciones.py — Revertir Operación
  12. operaciones.py — Límites Diarios
  13. operaciones.py — Validaciones de campos (DUI, NIT, email, teléfono)
  14. operaciones.py — Límite de préstamos simultáneos
  15. contabilidad.py — Reconciliación y balance partida doble
  16. Flujos de integración end-to-end

CÓMO EJECUTAR:
  cd BancoAppSistemas_fixed
  pytest tests/test_banco_completo.py -v
  pytest tests/ -v                        # ambas suites juntas
"""

import pytest
from decimal import Decimal
from datetime import datetime, date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    BaseLocal, Cliente, Movimiento, Prestamo, CuotaPrestamo,
    ConfigBanco, Usuario, AuditLog,
    TipoCuenta, Beneficiario, DepositoPlazoFijo, Garantia,
    ScoreCredito, TarjetaDebito, TarjetaCredito,
    Sucursal, ATM, AlertaAML, CierreDiario,
    Socio, AporteSocio,
)
from contabilidad import inicializar_contabilidad, saldo_cuenta, caja_real, reconciliar
from operaciones import (
    crear_cliente, depositar, retirar, transferir,
    otorgar_prestamo, pagar_prestamo,
    suspender_cliente, reactivar_cliente,
    revertir_operacion, verificar_limite_diario,
    OPERACIONES_REVERSIBLES, VENTANA_REVERSION_MIN,
)
from extras import (
    inicializar_tipos_cuenta, crear_tipo_cuenta,
    inicializar_sucursales, crear_sucursal,
    agregar_beneficiario, eliminar_beneficiario,
    crear_deposito_plazo, vencer_deposito_plazo,
    agregar_garantia, refinanciar_prestamo,
    calcular_score,
    emitir_tarjeta_debito, emitir_tarjeta_credito,
    verificar_aml, obtener_alertas_aml, revisar_alerta_aml,
    realizar_cierre_diario,
    generar_balance_general, generar_estado_resultados,
    registrar_socio, registrar_aporte,
    historial_cliente, actualizar_cuotas_vencidas,
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
    ok, c = crear_cliente(session, "Ana López", "Ahorro", 1000)
    session.commit()
    return c


@pytest.fixture
def cliente_b(session):
    ok, c = crear_cliente(session, "Carlos Mendez", "Ahorro", 500)
    session.commit()
    return c


@pytest.fixture
def cliente_rico(session):
    """Cliente con saldo alto para pruebas de DPF y tarjetas."""
    ok, c = crear_cliente(session, "Don Ricardo", "Ahorro", 10000)
    session.commit()
    return c


@pytest.fixture
def prestamo_activo(session, cliente_a):
    depositar(session, cliente_a.id, 5000)
    ok, p = otorgar_prestamo(session, cliente_a.id, 1000, 12)
    session.commit()
    return p


# ══════════════════════════════════════════════════════════════
# 1. TIPOS DE CUENTA
# ══════════════════════════════════════════════════════════════

class TestTiposCuenta:

    def test_inicializar_tipos_crea_defaults(self, session):
        inicializar_tipos_cuenta(session)
        tipos = session.query(TipoCuenta).all()
        assert len(tipos) >= 5
        nombres = [t.nombre for t in tipos]
        assert "Ahorro" in nombres
        assert "Corriente" in nombres
        assert "Infantil" in nombres

    def test_inicializar_tipos_es_idempotente(self, session):
        inicializar_tipos_cuenta(session)
        inicializar_tipos_cuenta(session)  # segunda vez no duplica
        count = session.query(TipoCuenta).count()
        assert count == 5

    def test_crear_tipo_cuenta_nuevo(self, session):
        ok, tc = crear_tipo_cuenta(session, "Premium", 0.05, 1000, False, "Cuenta premium")
        assert ok
        assert tc.nombre == "Premium"
        assert float(tc.tasa_interes) == pytest.approx(0.05)

    def test_crear_tipo_cuenta_duplicado_falla(self, session):
        inicializar_tipos_cuenta(session)
        ok, msg = crear_tipo_cuenta(session, "Ahorro", 0.03, 25, False)
        assert not ok
        assert "Ya existe" in msg

    def test_tipo_cuenta_saldo_minimo(self, session):
        ok, tc = crear_tipo_cuenta(session, "Empresarial Plus", 0.02, 5000, True)
        assert ok
        assert float(tc.saldo_minimo) == pytest.approx(5000.0)


# ══════════════════════════════════════════════════════════════
# 2. SUCURSALES Y ATM
# ══════════════════════════════════════════════════════════════

class TestSucursales:

    def test_inicializar_sucursales_crea_defaults(self, session):
        inicializar_sucursales(session)
        sucursales = session.query(Sucursal).all()
        assert len(sucursales) >= 4
        nombres = [s.nombre for s in sucursales]
        assert "Central" in nombres

    def test_inicializar_sucursales_crea_atm(self, session):
        inicializar_sucursales(session)
        atms = session.query(ATM).all()
        assert len(atms) >= 4  # un ATM por sucursal

    def test_inicializar_sucursales_idempotente(self, session):
        inicializar_sucursales(session)
        inicializar_sucursales(session)
        count = session.query(Sucursal).count()
        assert count == 4

    def test_crear_sucursal_nueva(self, session):
        ok, s = crear_sucursal(session, "Sonsonate", "Av. Central #10", "2450-0001")
        assert ok
        assert s.nombre == "Sonsonate"

    def test_crear_sucursal_duplicada_falla(self, session):
        crear_sucursal(session, "Norte", "Col. Escalón", "2200-0001")
        ok, msg = crear_sucursal(session, "Norte", "Otra dirección", "2200-0002")
        assert not ok
        assert "Ya existe" in msg


# ══════════════════════════════════════════════════════════════
# 3. BENEFICIARIOS FRECUENTES
# ══════════════════════════════════════════════════════════════

class TestBeneficiarios:

    def test_agregar_beneficiario_valido(self, session, cliente_a, cliente_b):
        ok, b = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Carlos")
        assert ok
        assert b.cuenta_destino == cliente_b.num_cuenta
        assert b.alias == "Carlos"

    def test_beneficiario_a_si_mismo_falla(self, session, cliente_a):
        ok, msg = agregar_beneficiario(session, cliente_a.id, cliente_a.num_cuenta, "Yo")
        assert not ok
        assert "ti mismo" in msg.lower()

    def test_beneficiario_cuenta_inexistente_falla(self, session, cliente_a):
        ok, msg = agregar_beneficiario(session, cliente_a.id, "XXXX-00000000", "Nadie")
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_beneficiario_duplicado_falla(self, session, cliente_a, cliente_b):
        agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Carlos")
        ok, msg = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Carlos2")
        assert not ok
        assert "ya está registrado" in msg.lower()

    def test_eliminar_beneficiario(self, session, cliente_a, cliente_b):
        ok, b = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Carlos")
        ok2, msg = eliminar_beneficiario(session, b.id, cliente_a.id)
        assert ok2
        assert "eliminado" in msg.lower()

    def test_eliminar_beneficiario_ajeno_falla(self, session, cliente_a, cliente_b):
        ok, b = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Carlos")
        ok2, msg = eliminar_beneficiario(session, b.id, cliente_b.id)  # otro cliente
        assert not ok2


# ══════════════════════════════════════════════════════════════
# 4. DEPÓSITO A PLAZO FIJO
# ══════════════════════════════════════════════════════════════

class TestDepositoPlazoFijo:

    def test_crear_dpf_basico(self, session, cliente_rico):
        saldo_antes = cliente_rico.saldo
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 12)
        assert ok
        assert dpf.monto == Decimal("1000.00")
        session.refresh(cliente_rico)
        assert cliente_rico.saldo == saldo_antes - Decimal("1000.00")

    def test_dpf_calcula_interes_correcto(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1200, 0.10, 12)
        assert ok
        # interes = 1200 * 0.10 * 12/12 = 120
        assert float(dpf.interes_proyectado) == pytest.approx(120.0, abs=0.01)
        assert float(dpf.monto_total) == pytest.approx(1320.0, abs=0.01)

    def test_dpf_monto_minimo_falla(self, session, cliente_rico):
        ok, msg = crear_deposito_plazo(session, cliente_rico.id, 100, 0.05, 6)
        assert not ok
        assert "500" in msg

    def test_dpf_plazo_cero_falla(self, session, cliente_rico):
        ok, msg = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 0)
        assert not ok
        assert "plazo" in msg.lower()

    def test_dpf_saldo_insuficiente_falla(self, session, cliente_a):
        # cliente_a tiene 1000, intenta DPF de 2000
        ok, msg = crear_deposito_plazo(session, cliente_a.id, 2000, 0.05, 6)
        assert not ok
        assert "saldo" in msg.lower()

    def test_dpf_cliente_inexistente_falla(self, session):
        ok, msg = crear_deposito_plazo(session, 9999, 1000, 0.05, 6)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_vencer_dpf_acredita_saldo(self, session, cliente_rico):
        saldo_antes = float(cliente_rico.saldo)
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 12)
        ok2, dpf2 = vencer_deposito_plazo(session, dpf.id)
        assert ok2
        session.refresh(cliente_rico)
        # El cliente recupera capital + intereses
        assert float(cliente_rico.saldo) == pytest.approx(saldo_antes - 1000 + float(dpf2.monto_total), abs=0.01)

    def test_vencer_dpf_cambia_estado(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 500, 0.05, 6)
        vencer_deposito_plazo(session, dpf.id)
        session.refresh(dpf)
        assert dpf.estado == "VENCIDO"

    def test_vencer_dpf_ya_vencido_falla(self, session, cliente_rico):
        ok, dpf = crear_deposito_plazo(session, cliente_rico.id, 500, 0.05, 6)
        vencer_deposito_plazo(session, dpf.id)
        ok2, msg = vencer_deposito_plazo(session, dpf.id)
        assert not ok2
        assert "activo" in msg.lower()

    def test_vencer_dpf_inexistente_falla(self, session):
        ok, msg = vencer_deposito_plazo(session, 9999)
        assert not ok


# ══════════════════════════════════════════════════════════════
# 5. GARANTÍAS Y REFINANCIAMIENTO
# ══════════════════════════════════════════════════════════════

class TestGarantiasRefinanciamiento:

    def test_agregar_garantia_a_prestamo(self, session, prestamo_activo):
        ok, g = agregar_garantia(session, prestamo_activo.id, "Inmueble",
                                  "Casa en Col. Escalón", 50000, "REG-001")
        assert ok
        assert float(g.valor_estimado) == pytest.approx(50000.0)
        assert g.tipo == "Inmueble"

    def test_garantia_prestamo_inexistente_falla(self, session):
        ok, msg = agregar_garantia(session, 9999, "Vehículo", "Pick-up 2020", 15000)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_refinanciar_prestamo_activo(self, session, prestamo_activo):
        ok, nuevo = refinanciar_prestamo(session, prestamo_activo.id, 24)
        assert ok
        assert nuevo.plazo_meses == 24
        session.refresh(prestamo_activo)
        assert prestamo_activo.estado == "REFINANCIADO"

    def test_refinanciar_genera_nuevas_cuotas(self, session, prestamo_activo):
        ok, nuevo = refinanciar_prestamo(session, prestamo_activo.id, 6)
        assert ok
        cuotas = session.query(CuotaPrestamo).filter_by(prestamo_id=nuevo.id).all()
        assert len(cuotas) == 6

    def test_refinanciar_prestamo_inexistente_falla(self, session):
        ok, msg = refinanciar_prestamo(session, 9999, 12)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_refinanciar_prestamo_pagado_falla(self, session, cliente_a, prestamo_activo):
        # Pagar todo el préstamo
        depositar(session, cliente_a.id, 5000)
        session.commit()
        pagar_prestamo(session, cliente_a.id, float(prestamo_activo.saldo_pendiente) + 100)
        session.commit()
        session.refresh(prestamo_activo)
        ok, msg = refinanciar_prestamo(session, prestamo_activo.id, 12)
        assert not ok


# ══════════════════════════════════════════════════════════════
# 6. SCORE CREDITICIO
# ══════════════════════════════════════════════════════════════

class TestScoreCrediticio:

    def test_score_cliente_nuevo(self, session, cliente_a):
        sc = calcular_score(session, cliente_a.id)
        assert sc is not None
        assert 300 <= sc.score <= 850

    def test_score_base_sin_prestamos_es_500(self, session, cliente_a):
        sc = calcular_score(session, cliente_a.id)
        # Sin préstamos ni historial, score base = 500
        assert sc.score == 500

    def test_score_sube_con_saldo_alto(self, session, cliente_rico):
        sc = calcular_score(session, cliente_rico.id)
        # saldo > 5000 => +100 + +50 = +150 sobre base 500
        assert sc.score >= 650

    def test_score_cliente_inexistente_retorna_none(self, session):
        resultado = calcular_score(session, 9999)
        assert resultado is None

    def test_score_se_actualiza_segunda_vez(self, session, cliente_a):
        sc1 = calcular_score(session, cliente_a.id)
        score1 = sc1.score
        depositar(session, cliente_a.id, 10000)
        session.commit()
        sc2 = calcular_score(session, cliente_a.id)
        # El mismo objeto debe actualizarse, no duplicarse
        count = session.query(ScoreCredito).filter_by(cliente_id=cliente_a.id).count()
        assert count == 1

    def test_score_guarda_categoria(self, session, cliente_a):
        sc = calcular_score(session, cliente_a.id)
        assert sc.categoria in ("Excelente", "Bueno", "Regular", "Malo", "Muy Malo")

    def test_score_actualiza_campo_en_cliente(self, session, cliente_a):
        calcular_score(session, cliente_a.id)
        session.refresh(cliente_a)
        assert cliente_a.score_credito is not None


# ══════════════════════════════════════════════════════════════
# 7. TARJETAS
# ══════════════════════════════════════════════════════════════

class TestTarjetas:

    def test_emitir_tarjeta_debito(self, session, cliente_a):
        ok, td = emitir_tarjeta_debito(session, cliente_a.id)
        assert ok
        assert td.cliente_id == cliente_a.id
        assert td.numero is not None
        assert len(td.numero) == 16

    def test_tarjeta_debito_tiene_cvv(self, session, cliente_a):
        ok, td = emitir_tarjeta_debito(session, cliente_a.id)
        assert ok
        assert td.cvv is not None
        assert len(td.cvv) == 3

    def test_emitir_tarjeta_debito_cliente_inexistente_falla(self, session):
        ok, msg = emitir_tarjeta_debito(session, 9999)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_emitir_tarjeta_credito_sin_score(self, session, cliente_a):
        # Sin score calculado, score_credito es None => debe emitirse
        ok, tc = emitir_tarjeta_credito(session, cliente_a.id, 2000)
        assert ok
        assert float(tc.limite) == pytest.approx(2000.0)

    def test_emitir_tarjeta_credito_score_bajo_falla(self, session, cliente_a):
        # Fijar score bajo manualmente
        cliente_a.score_credito = 400
        session.flush()
        ok, msg = emitir_tarjeta_credito(session, cliente_a.id, 1000)
        assert not ok
        assert "500" in msg

    def test_emitir_tarjeta_credito_score_alto_ok(self, session, cliente_rico):
        calcular_score(session, cliente_rico.id)
        session.refresh(cliente_rico)
        ok, tc = emitir_tarjeta_credito(session, cliente_rico.id, 5000)
        assert ok

    def test_tarjeta_credito_cliente_inexistente_falla(self, session):
        ok, msg = emitir_tarjeta_credito(session, 9999, 1000)
        assert not ok


# ══════════════════════════════════════════════════════════════
# 8. AML — ANTI LAVADO DE DINERO
# ══════════════════════════════════════════════════════════════

class TestAML:

    def test_monto_alto_genera_alerta_critica(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 15000, "Depósito")
        assert len(alertas) == 1
        assert alertas[0].tipo == "MONTO_ALTO"
        assert alertas[0].nivel == "CRITICA"

    def test_monto_bajo_no_genera_alerta(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 500, "Depósito")
        assert len(alertas) == 0

    def test_monto_exacto_umbral_genera_alerta(self, session, cliente_a):
        # Exactamente en el umbral (10000) debe generar alerta
        alertas = verificar_aml(session, cliente_a.id, 10000, "Depósito")
        assert len(alertas) == 1

    def test_multiples_transferencias_genera_alerta(self, session, cliente_a, cliente_b):
        # Crear 5 transferencias para el mismo cliente hoy
        depositar(session, cliente_a.id, 50000)
        session.commit()
        for _ in range(5):
            transferir(session, cliente_a.id, cliente_b.id, 100)
            session.commit()
        # La 6ta transferencia dispara alerta de múltiples
        alertas = verificar_aml(session, cliente_a.id, 100, "transferencia")
        aml_tipos = [a.tipo for a in alertas]
        assert "MULT_TRANSFERENCIAS" in aml_tipos

    def test_obtener_alertas_pendientes(self, session, cliente_a):
        verificar_aml(session, cliente_a.id, 12000, "Depósito")
        session.flush()
        alertas = obtener_alertas_aml(session, "PENDIENTE")
        assert len(alertas) >= 1

    def test_obtener_alertas_todas(self, session, cliente_a):
        verificar_aml(session, cliente_a.id, 12000, "Depósito")
        session.flush()
        alertas = obtener_alertas_aml(session, "TODAS")
        assert len(alertas) >= 1

    def test_revisar_alerta_aml(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 20000, "Depósito")
        session.flush()
        alerta = alertas[0]
        ok, a = revisar_alerta_aml(session, alerta.id, "AuditorJuan", "Revisado", cerrar=False)
        assert ok
        assert a.estado == "REVISADA"
        assert a.revisado_por == "AuditorJuan"

    def test_cerrar_alerta_aml(self, session, cliente_a):
        alertas = verificar_aml(session, cliente_a.id, 20000, "Depósito")
        session.flush()
        ok, a = revisar_alerta_aml(session, alertas[0].id, "AuditorJuan", cerrar=True)
        assert ok
        assert a.estado == "CERRADA"

    def test_revisar_alerta_inexistente_falla(self, session):
        ok, msg = revisar_alerta_aml(session, 9999, "Auditor")
        assert not ok


# ══════════════════════════════════════════════════════════════
# 9. CIERRE DIARIO
# ══════════════════════════════════════════════════════════════

class TestCierreDiario:

    def test_cierre_diario_basico(self, session, cliente_a):
        depositar(session, cliente_a.id, 200)
        session.commit()
        ok, cierre = realizar_cierre_diario(session, "AdminTest", "Cierre prueba")
        assert ok
        assert cierre.realizado_por == "AdminTest"

    def test_cierre_diario_solo_uno_por_dia(self, session, cliente_a):
        realizar_cierre_diario(session, "Admin1")
        session.commit()
        ok, msg = realizar_cierre_diario(session, "Admin2")
        assert not ok
        assert "ya se realizó" in msg.lower()

    def test_cierre_registra_totales(self, session, cliente_a):
        depositar(session, cliente_a.id, 500)
        session.commit()
        ok, cierre = realizar_cierre_diario(session, "Admin")
        assert ok
        assert float(cierre.total_depositos) >= 500


# ══════════════════════════════════════════════════════════════
# 10. BALANCE GENERAL Y ESTADO DE RESULTADOS
# ══════════════════════════════════════════════════════════════

class TestEstadosFinancieros:

    def test_balance_general_tiene_estructura(self, session):
        bg = generar_balance_general(session)
        assert "activos" in bg
        assert "pasivos" in bg
        assert "patrimonio" in bg
        assert "ecuacion_ok" in bg

    def test_balance_general_ecuacion_cuadrada(self, session, cliente_a):
        depositar(session, cliente_a.id, 1000)
        session.commit()
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"]

    def test_balance_general_con_operaciones(self, session, cliente_a, cliente_b):
        depositar(session, cliente_a.id, 2000)
        transferir(session, cliente_a.id, cliente_b.id, 500)
        session.commit()
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"]

    def test_estado_resultados_tiene_estructura(self, session):
        er = generar_estado_resultados(session)
        assert "ingresos" in er
        assert "gastos" in er
        assert "utilidad" in er

    def test_estado_resultados_ingresos_positivos_tras_deposito(self, session, cliente_a):
        depositar(session, cliente_a.id, 1000)
        session.commit()
        er = generar_estado_resultados(session)
        assert er["total_ingresos"] >= 0


# ══════════════════════════════════════════════════════════════
# 11. SOCIOS Y APORTES
# ══════════════════════════════════════════════════════════════

class TestSocios:

    def test_registrar_socio(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id)
        assert ok
        assert socio.cliente_id == cliente_a.id
        assert socio.numero_socio.startswith("SOC-")

    def test_registrar_socio_duplicado_falla(self, session, cliente_a):
        registrar_socio(session, cliente_a.id)
        ok, msg = registrar_socio(session, cliente_a.id)
        assert not ok
        assert "ya es socio" in msg.lower()

    def test_registrar_socio_cliente_inexistente_falla(self, session):
        ok, msg = registrar_socio(session, 9999)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_registrar_socio_con_aporte_inicial(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id, aporte_inicial=200)
        assert ok
        aportes = session.query(AporteSocio).filter_by(socio_id=socio.id).all()
        assert len(aportes) == 1
        assert float(aportes[0].monto) == pytest.approx(200.0)

    def test_registrar_aporte_adicional(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id)
        ok2, aporte = registrar_aporte(session, socio.id, 300, "EXTRAORDINARIO", "Bono")
        assert ok2
        assert float(aporte.monto) == pytest.approx(300.0)
        assert aporte.tipo == "EXTRAORDINARIO"

    def test_aporte_acumula_total_socio(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id, aporte_inicial=100)
        registrar_aporte(session, socio.id, 200)
        session.refresh(socio)
        assert float(socio.aporte_total) == pytest.approx(300.0)

    def test_aporte_monto_cero_falla(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id)
        ok2, msg = registrar_aporte(session, socio.id, 0)
        assert not ok2
        assert "positivo" in msg.lower()

    def test_aporte_socio_inexistente_falla(self, session):
        ok, msg = registrar_aporte(session, 9999, 100)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_aporte_genera_asiento_contable(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id)
        saldo_capital_antes = saldo_cuenta(session, "Capital Banco")
        registrar_aporte(session, socio.id, 500)
        session.commit()
        saldo_capital_despues = saldo_cuenta(session, "Capital Banco")
        assert saldo_capital_despues > saldo_capital_antes


# ══════════════════════════════════════════════════════════════
# 12. HISTORIAL CLIENTE Y CUOTAS VENCIDAS
# ══════════════════════════════════════════════════════════════

class TestHistorialYCuotas:

    def test_historial_cliente_estructura(self, session, cliente_a):
        h = historial_cliente(session, cliente_a.id)
        assert h is not None
        assert "cliente" in h
        assert "movimientos" in h
        assert "prestamos" in h
        assert "tarjetas_debito" in h
        assert "tarjetas_credito" in h
        assert "depositos_plazo" in h
        assert "beneficiarios" in h

    def test_historial_incluye_movimientos(self, session, cliente_a):
        depositar(session, cliente_a.id, 200)
        session.commit()
        h = historial_cliente(session, cliente_a.id)
        assert len(h["movimientos"]) >= 1

    def test_historial_incluye_prestamos(self, session, prestamo_activo, cliente_a):
        h = historial_cliente(session, cliente_a.id)
        assert len(h["prestamos"]) >= 1

    def test_historial_cliente_inexistente_retorna_none(self, session):
        h = historial_cliente(session, 9999)
        assert h is None

    def test_actualizar_cuotas_vencidas(self, session, cliente_a):
        depositar(session, cliente_a.id, 5000)
        ok, p = otorgar_prestamo(session, cliente_a.id, 1200, 12)
        session.commit()
        # Forzar vencimiento de cuotas
        cuotas = session.query(CuotaPrestamo).filter_by(prestamo_id=p.id).all()
        for c in cuotas[:3]:
            c.fecha_vencimiento = date.today() - timedelta(days=10)
        session.commit()
        count = actualizar_cuotas_vencidas(session)
        assert count == 3
        vencidas = session.query(CuotaPrestamo).filter_by(
            prestamo_id=p.id, estado="VENCIDA"
        ).count()
        assert vencidas == 3

    def test_actualizar_cuotas_sin_vencidas(self, session, cliente_a):
        # Sin cuotas vencidas, debe retornar 0
        count = actualizar_cuotas_vencidas(session)
        assert count == 0


# ══════════════════════════════════════════════════════════════
# 13. REVERSIÓN DE OPERACIONES
# ══════════════════════════════════════════════════════════════

class TestReversionOperaciones:

    def test_revertir_deposito(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        ok_dep, dep = depositar(session, cliente_a.id, 300)
        session.commit()
        mov = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Deposito"
        ).order_by(Movimiento.id.desc()).first()
        ok, msg = revertir_operacion(session, mov.id, "Test reverso")
        print("OK =", ok)
        print("MSG =", msg)
        assert ok
        session.refresh(cliente_a)
        assert float(cliente_a.saldo) == pytest.approx(saldo_antes, abs=0.01)

    def test_revertir_retiro(self, session, cliente_a):
        saldo_antes = float(cliente_a.saldo)
        retirar(session, cliente_a.id, 200)
        session.commit()
        mov = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Retiro"
        ).order_by(Movimiento.id.desc()).first()
        ok, msg = revertir_operacion(session, mov.id)
        assert ok
        session.refresh(cliente_a)
        assert float(cliente_a.saldo) == pytest.approx(saldo_antes, abs=0.01)

    def test_revertir_dos_veces_falla(self, session, cliente_a):
        depositar(session, cliente_a.id, 100)
        session.commit()
        mov = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Deposito"
        ).order_by(Movimiento.id.desc()).first()
        revertir_operacion(session, mov.id)
        ok, msg = revertir_operacion(session, mov.id)
        assert not ok
        assert "ya fue revertida" in msg.lower()

    def test_revertir_movimiento_inexistente_falla(self, session):
        ok, msg = revertir_operacion(session, 99999)
        assert not ok
        assert "no encontrado" in msg.lower()

    def test_no_revertir_pago_prestamo(self, session, prestamo_activo, cliente_a):
        depositar(session, cliente_a.id, 5000)
        session.commit()
        pagar_prestamo(session, cliente_a.id, 100)
        session.commit()
        mov = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Pago Prestamo"
        ).order_by(Movimiento.id.desc()).first()
        if mov:
            ok, msg = revertir_operacion(session, mov.id)
            assert not ok
            assert "reversible" in msg.lower() or "no es reversible" in msg.lower()

    def test_revertir_operacion_antigua_falla(self, session, cliente_a):
        depositar(session, cliente_a.id, 100)
        session.commit()
        mov = session.query(Movimiento).filter_by(
            cliente_id=cliente_a.id, tipo="Deposito"
        ).first()
        # Simular que el movimiento es antiguo
        mov.fecha = datetime.utcnow() - timedelta(minutes=VENTANA_REVERSION_MIN + 5)
        session.commit()
        ok, msg = revertir_operacion(session, mov.id)
        assert not ok
        assert "minutos" in msg.lower()


# ══════════════════════════════════════════════════════════════
# 14. LÍMITES DIARIOS
# ══════════════════════════════════════════════════════════════

class TestLimitesDiarios:

    def test_retiro_dentro_del_limite(self, session, cliente_a):
        ok, msg = verificar_limite_diario(session, cliente_a.id, "Retiro", 500)
        assert ok

    def test_deposito_no_tiene_limite(self, session, cliente_a):
        # Depósito no tiene límite configurado
        ok, msg = verificar_limite_diario(session, cliente_a.id, "Deposito", 999999)
        assert ok

    def test_retiro_supera_limite_falla(self, session, cliente_a):
        # Configurar límite bajo
        cfg = ConfigBanco(clave="limite_retiro_diario", valor="100.00")
        session.add(cfg)
        session.commit()
        # Hacer un retiro de 80 primero
        depositar(session, cliente_a.id, 500)
        session.commit()
        retirar(session, cliente_a.id, 80)
        session.commit()
        # Intentar retirar 50 más (total 130 > 100)
        ok, msg = verificar_limite_diario(session, cliente_a.id, "Retiro", 50)
        assert not ok
        assert "límite" in msg.lower() or "limite" in msg.lower()

    def test_transferencia_dentro_del_limite(self, session, cliente_a):
        ok, msg = verificar_limite_diario(session, cliente_a.id, "Transferencia Enviada", 1000)
        assert ok

    def test_transferencia_supera_limite_falla(self, session, cliente_a, cliente_b):
        # Configurar límite de transferencia muy bajo
        cfg = ConfigBanco(clave="limite_transferencia_diaria", valor="200.00")
        session.add(cfg)
        session.commit()
        depositar(session, cliente_a.id, 5000)
        session.commit()
        transferir(session, cliente_a.id, cliente_b.id, 150)
        session.commit()
        ok, msg = verificar_limite_diario(session, cliente_a.id, "Transferencia Enviada", 100)
        assert not ok


# ══════════════════════════════════════════════════════════════
# 15. CONTABILIDAD — RECONCILIACIÓN
# ══════════════════════════════════════════════════════════════

class TestContabilidadAvanzada:

    def test_reconciliar_retorna_resultado(self, session):
        resultado = reconciliar(session)
        # Debe retornar un dict o similar sin error
        assert resultado is not None

    def test_saldo_caja_aumenta_con_deposito(self, session, cliente_a):
        caja_antes = caja_real(session)
        depositar(session, cliente_a.id, 500)
        session.commit()
        caja_despues = caja_real(session)
        assert caja_despues > caja_antes

    def test_saldo_caja_disminuye_con_retiro(self, session, cliente_a):
        depositar(session, cliente_a.id, 1000)
        session.commit()
        caja_antes = caja_real(session)
        retirar(session, cliente_a.id, 300)
        session.commit()
        caja_despues = caja_real(session)
        assert caja_despues < caja_antes

    def test_balance_cuadrado_con_dpf(self, session, cliente_rico):
        crear_deposito_plazo(session, cliente_rico.id, 1000, 0.05, 6)
        session.commit()
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"]

    def test_balance_cuadrado_con_socio(self, session, cliente_a):
        ok, socio = registrar_socio(session, cliente_a.id)
        registrar_aporte(session, socio.id, 300)
        session.commit()
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"]


# ══════════════════════════════════════════════════════════════
# 16. FLUJOS DE INTEGRACIÓN END-TO-END
# ══════════════════════════════════════════════════════════════

class TestIntegracionCompleta:

    def test_flujo_completo_plazo_fijo(self, session):
        """Crear cliente → depositar → crear DPF → vencer DPF → verificar saldo final."""
        ok, cliente = crear_cliente(session, "Inversora SA", "Ahorro", 5000)
        session.commit()
        ok, dpf = crear_deposito_plazo(session, cliente.id, 2000, 0.08, 12)
        session.commit()
        assert ok
        saldo_tras_dpf = float(cliente.saldo)
        assert saldo_tras_dpf == pytest.approx(3000.0, abs=0.01)
        ok2, dpf2 = vencer_deposito_plazo(session, dpf.id)
        session.commit()
        session.refresh(cliente)
        # Debe tener 3000 + capital(2000) + intereses
        assert float(cliente.saldo) > 5000

    def test_flujo_completo_socio_y_score(self, session):
        """Crear cliente → hacerlo socio → aportar → calcular score."""
        ok, cliente = crear_cliente(session, "Coopista", "Ahorro", 2000)
        session.commit()
        ok, socio = registrar_socio(session, cliente.id, aporte_inicial=500)
        session.commit()
        assert ok
        registrar_aporte(session, socio.id, 300, "EXTRAORDINARIO")
        session.commit()
        session.refresh(socio)
        assert float(socio.aporte_total) == pytest.approx(800.0, abs=0.01)
        sc = calcular_score(session, cliente.id)
        assert sc is not None

    def test_flujo_aml_y_revision(self, session):
        """Operación de monto alto → verificar AML → revisar alerta → cerrar."""
        ok, cliente = crear_cliente(session, "Sospechoso", "Ahorro", 100000)
        session.commit()
        alertas = verificar_aml(session, cliente.id, 50000, "Depósito masivo")
        session.flush()
        assert len(alertas) >= 1
        ok, alerta = revisar_alerta_aml(session, alertas[0].id, "ComplianceOfficer",
                                         "Revisado OK", cerrar=True)
        assert ok
        assert alerta.estado == "CERRADA"

    def test_flujo_tarjeta_con_score(self, session):
        """Crear cliente con buen saldo → calcular score → emitir tarjeta crédito."""
        ok, cliente = crear_cliente(session, "Cliente VIP", "Ahorro", 20000)
        session.commit()
        sc = calcular_score(session, cliente.id)
        assert sc.score >= 500
        ok, tc = emitir_tarjeta_credito(session, cliente.id, 10000)
        assert ok
        assert float(tc.limite) == pytest.approx(10000.0)

    def test_flujo_beneficiario_y_transferencia(self, session, cliente_a, cliente_b):
        """Agregar beneficiario → transferir → verificar historial."""
        ok, b = agregar_beneficiario(session, cliente_a.id, cliente_b.num_cuenta, "Mi amigo")
        assert ok
        ok_t, msg_t = transferir(session, cliente_a.id, cliente_b.id, 200)
        session.commit()
        assert ok_t
        h = historial_cliente(session, cliente_a.id)
        tipos = [m.tipo for m in h["movimientos"]]
        assert "Transferencia Enviada" in tipos
        bens = h["beneficiarios"]
        assert len(bens) >= 1

    def test_cierre_diario_con_multiples_operaciones(self, session):
        """Múltiples operaciones del día → cierre diario → verificar totales."""
        ok, c1 = crear_cliente(session, "Cliente Cierre 1", "Ahorro", 2000)
        ok, c2 = crear_cliente(session, "Cliente Cierre 2", "Ahorro", 1000)
        session.commit()
        depositar(session, c1.id, 500)
        retirar(session, c1.id, 100)
        transferir(session, c1.id, c2.id, 200)
        session.commit()
        ok, cierre = realizar_cierre_diario(session, "AdminCierre", "Cierre end-to-end")
        assert ok
        assert float(cierre.total_depositos) >= 500
        assert float(cierre.total_retiros) >= 100

    def test_stress_extras_100_operaciones(self, session):
        """100 operaciones mixtas sin errores ni balance roto."""
        ok, c1 = crear_cliente(session, "Stress Extra A", "Ahorro", 100000)
        ok, c2 = crear_cliente(session, "Stress Extra B", "Ahorro", 100000)
        session.commit()
        for i in range(50):
            depositar(session, c1.id, 100)
        for i in range(30):
            retirar(session, c1.id, 50)
        for i in range(20):
            transferir(session, c1.id, c2.id, 75)
        session.commit()
        bg = generar_balance_general(session)
        assert bg["ecuacion_ok"]