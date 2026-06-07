"""
auth.py — Sistema de autenticación y control de acceso por roles.

Roles y permisos:
  ADMIN   → acceso total: gestionar usuarios + todas las operaciones
  CAJERO  → depósitos, retiros, transferencias, ver clientes e historial
  GERENTE → reportes, reconciliación, préstamos, ver todo (solo lectura en operaciones)
  AUDITOR → solo lectura: panel, reportes, historial, reconciliación
"""

import bcrypt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models import Usuario, AuditLog


# ─── Permisos por rol ────────────────────────────────────────

PERMISOS = {
    "ADMIN": {
        "ver_panel", "ver_clientes", "crear_cliente", "editar_cliente",
        "gestionar_estado_cliente",
        "operaciones_bancarias",
        "ver_prestamos", "gestionar_prestamos",
        "ver_reportes", "ver_reconciliacion",
        "gestionar_usuarios",
        "ver_auditlog",
        "configurar_banco",
        "ver_alertas",
        # nuevos
        "ver_tarjetas", "gestionar_tarjetas",
        "ver_sucursales", "gestionar_sucursales",
        "ver_plazo_fijo", "gestionar_plazo_fijo",
        "ver_aml", "gestionar_aml",
        "ver_socios", "gestionar_socios",
        "ver_dashboard_gerencial",
        "realizar_cierre_diario",
        "ver_balance_general",
        "reportes",          # contabilidad avanzada
    },
    "GERENTE": {
        "ver_panel", "ver_clientes",
        "ver_prestamos", "gestionar_prestamos",
        "ver_reportes", "ver_reconciliacion",
        "ver_auditlog",
        "ver_alertas",
        "ver_tarjetas",
        "ver_sucursales",
        "ver_plazo_fijo",
        "ver_aml", "gestionar_aml",
        "ver_socios",
        "ver_dashboard_gerencial",
        "realizar_cierre_diario",
        "ver_balance_general",
        "reportes",          # contabilidad avanzada
    },
    "CAJERO": {
        "ver_panel", "ver_clientes", "crear_cliente", "editar_cliente",
        "operaciones_bancarias",
        "ver_prestamos",
        "ver_tarjetas", "gestionar_tarjetas",
        "ver_plazo_fijo", "gestionar_plazo_fijo",
        "ver_socios", "gestionar_socios",
    },
    "AUDITOR": {
        "ver_panel", "ver_reportes", "ver_reconciliacion",
        "ver_clientes", "ver_auditlog",
        "ver_aml",
        "ver_balance_general",
        "reportes",          # contabilidad avanzada (solo lectura)
    },
}

MENU_POR_ROL = {
    "ADMIN":   ["📊 Panel principal", "👥 Clientes", "💵 Operaciones bancarias",
                "🏷️ Préstamos", "💳 Tarjetas", "🏦 Sucursales & ATM",
                "📑 Plazo Fijo", "🤝 Socios", "🚨 Monitor AML",
                "🏦 Contabilidad Avanzada",
                "📈 Reportes contables", "📊 Dashboard Gerencial",
                "🔍 Reconciliación", "⚙️ Configuración", "🔔 Alertas",
                "👤 Gestión de usuarios", "📋 Log de auditoría"],
    "GERENTE": ["📊 Panel principal", "👥 Clientes", "🏷️ Préstamos",
                "💳 Tarjetas", "🏦 Sucursales & ATM", "📑 Plazo Fijo", "🤝 Socios",
                "🚨 Monitor AML", "🏦 Contabilidad Avanzada",
                "📈 Reportes contables",
                "📊 Dashboard Gerencial", "🔍 Reconciliación",
                "🔔 Alertas", "📋 Log de auditoría"],
    "CAJERO":  ["📊 Panel principal", "👥 Clientes", "💵 Operaciones bancarias",
                "🏷️ Préstamos", "💳 Tarjetas", "📑 Plazo Fijo", "🤝 Socios"],
    "AUDITOR": ["📊 Panel principal", "👥 Clientes", "📈 Reportes contables",
                "🚨 Monitor AML", "🏦 Contabilidad Avanzada",
                "🔍 Reconciliación", "📋 Log de auditoría"],
}

ROL_COLOR = {
    "ADMIN":   "#7F77DD",
    "GERENTE": "#D85A30",
    "CAJERO":  "#1D9E75",
    "AUDITOR": "#378ADD",
}


# ─── Helpers ─────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def tiene_permiso(usuario, permiso: str) -> bool:
    if not usuario or not usuario.activo:
        return False
    return permiso in PERMISOS.get(usuario.rol, set())


# ─── Auditoría ───────────────────────────────────────────────

def registrar_log(session, usuario, accion: str, detalle: str = "", resultado: str = "OK"):
    log = AuditLog(
        usuario_id=usuario.id if usuario else None,
        username=usuario.username if usuario else "sistema",
        rol=usuario.rol if usuario else "—",
        accion=accion,
        detalle=detalle,
        resultado=resultado,
        fecha=datetime.utcnow(),
    )
    session.add(log)
    try:
        session.flush()
    except Exception:
        pass


# ─── Login ───────────────────────────────────────────────────

MAX_INTENTOS    = 5
BLOQUEO_MINUTOS = 15

def login(session, username: str, password: str):
    username = username.strip().lower()
    if not username or not password:
        return None, "Usuario y contraseña requeridos"

    u = session.query(Usuario).filter_by(username=username).first()
    if not u:
        return None, "Usuario no encontrado"
    if not u.activo:
        return None, "Usuario desactivado. Contacte al administrador."

    # ── Verificar bloqueo temporal ──
    if u.bloqueado_hasta and datetime.utcnow() < u.bloqueado_hasta:
        restante = int((u.bloqueado_hasta - datetime.utcnow()).total_seconds() / 60) + 1
        registrar_log(session, u, "LOGIN_BLOQUEADO",
                      f"Intento mientras cuenta bloqueada ({restante} min restantes)", "ERROR")
        session.commit()
        return None, (f"Cuenta bloqueada por {restante} minuto(s) por múltiples "
                      f"intentos fallidos. Intente más tarde.")

    if not verify_password(password, u.password_hash):
        u.intentos_fallidos = (u.intentos_fallidos or 0) + 1
        if u.intentos_fallidos >= MAX_INTENTOS:
            u.bloqueado_hasta = datetime.utcnow() + timedelta(minutes=BLOQUEO_MINUTOS)
            registrar_log(session, u, "CUENTA_BLOQUEADA",
                          f"Bloqueada tras {u.intentos_fallidos} intentos fallidos", "ERROR")
            session.commit()
            return None, (f"Cuenta bloqueada por {BLOQUEO_MINUTOS} minutos tras "
                          f"{MAX_INTENTOS} intentos fallidos.")
        restantes = MAX_INTENTOS - u.intentos_fallidos
        registrar_log(session, u, "LOGIN_FALLIDO",
                      f"Intento fallido ({u.intentos_fallidos}/{MAX_INTENTOS})", "ERROR")
        session.commit()
        return None, f"Contraseña incorrecta. {restantes} intento(s) restante(s) antes del bloqueo."

    # ── Login exitoso: resetear contadores ──
    u.intentos_fallidos = 0
    u.bloqueado_hasta   = None
    u.ultimo_login      = datetime.utcnow()
    registrar_log(session, u, "LOGIN", f"Sesión iniciada — rol: {u.rol}", "OK")
    session.commit()
    return u, f"Bienvenido, {u.nombre}"


# ─── CRUD usuarios ───────────────────────────────────────────

def crear_usuario(session, actor, username: str, nombre: str, password: str, rol: str):
    if actor and not tiene_permiso(actor, "gestionar_usuarios"):
        return False, "Sin permiso para crear usuarios"

    username = username.strip().lower()
    nombre   = nombre.strip().title()
    rol      = rol.upper()

    if not username or not nombre or not password:
        return False, "Todos los campos son requeridos"
    if len(password) < 6:
        return False, "La contraseña debe tener al menos 6 caracteres"
    if rol not in PERMISOS:
        return False, f"Rol inválido: {rol}"
    if session.query(Usuario).filter_by(username=username).first():
        return False, f"El usuario '{username}' ya existe"

    u = Usuario(
        username=username,
        nombre=nombre,
        password_hash=hash_password(password),
        rol=rol,
        activo=True,
    )
    session.add(u)
    try:
        session.flush()
        registrar_log(session, actor, "CREAR_USUARIO",
                      f"Usuario '{username}' creado con rol {rol}", "OK")
        session.commit()
        return True, f"Usuario '{username}' ({rol}) creado correctamente"
    except Exception as e:
        session.rollback()
        return False, f"Error: {str(e)}"


def cambiar_password(session, actor, target_id: int, nueva: str, actual: str = None):
    target = session.query(Usuario).filter_by(id=target_id).first()
    if not target:
        return False, "Usuario no encontrado"

    es_propio = actor.id == target_id
    es_admin  = tiene_permiso(actor, "gestionar_usuarios")

    if not es_admin and not es_propio:
        return False, "Sin permiso"
    if es_propio and not es_admin:
        if not actual or not verify_password(actual, target.password_hash):
            return False, "La contraseña actual es incorrecta"
    if len(nueva) < 6:
        return False, "Mínimo 6 caracteres"

    target.password_hash = hash_password(nueva)
    registrar_log(session, actor, "CAMBIO_PASSWORD",
                  f"Contraseña cambiada para '{target.username}'", "OK")
    try:
        session.commit()
        return True, "Contraseña actualizada correctamente"
    except Exception as e:
        session.rollback()
        return False, str(e)


def toggle_usuario(session, actor, target_id: int):
    if not tiene_permiso(actor, "gestionar_usuarios"):
        return False, "Sin permiso"
    if actor.id == target_id:
        return False, "No puedes desactivarte a ti mismo"

    target = session.query(Usuario).filter_by(id=target_id).first()
    if not target:
        return False, "Usuario no encontrado"

    target.activo = not target.activo
    accion = "ACTIVAR_USUARIO" if target.activo else "DESACTIVAR_USUARIO"
    registrar_log(session, actor, accion,
                  f"Usuario '{target.username}' {'activado' if target.activo else 'desactivado'}", "OK")
    try:
        session.commit()
        estado = "activado" if target.activo else "desactivado"
        return True, f"Usuario '{target.username}' {estado}"
    except Exception as e:
        session.rollback()
        return False, str(e)


def cambiar_rol(session, actor, target_id: int, nuevo_rol: str):
    if not tiene_permiso(actor, "gestionar_usuarios"):
        return False, "Sin permiso"
    if actor.id == target_id:
        return False, "No puedes cambiar tu propio rol"
    nuevo_rol = nuevo_rol.upper()
    if nuevo_rol not in PERMISOS:
        return False, f"Rol inválido"

    target = session.query(Usuario).filter_by(id=target_id).first()
    if not target:
        return False, "Usuario no encontrado"

    rol_anterior = target.rol
    target.rol = nuevo_rol
    registrar_log(session, actor, "CAMBIO_ROL",
                  f"'{target.username}': {rol_anterior} → {nuevo_rol}", "OK")
    try:
        session.commit()
        return True, f"Rol de '{target.username}' cambiado a {nuevo_rol}"
    except Exception as e:
        session.rollback()
        return False, str(e)


# ─── Bootstrap ───────────────────────────────────────────────

def hay_usuarios(session) -> bool:
    return session.query(Usuario).count() > 0


def registrar_primer_admin(session, username: str, nombre: str, password: str):
    if hay_usuarios(session):
        return False, "Ya existen usuarios registrados"
    return crear_usuario(session, None, username, nombre, password, "ADMIN")
