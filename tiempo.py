from datetime import date, datetime

# ── Cambia esto para simular fechas ──
FECHA_SIMULADA = date(2027, 12, 1)  
# None = fecha real
# Ejemplo para simular 6 meses después:
# FECHA_SIMULADA = date(2026, 12, 1)

def hoy():
    return FECHA_SIMULADA if FECHA_SIMULADA else date.today()
