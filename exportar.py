"""
exportar.py — Exportación de PDF y CSV para el Sistema Bancario
Genera: estado de cuenta, comprobante de transacción, tabla de movimientos CSV,
        tabla de amortización PDF.
"""

import io
import csv
from datetime import datetime
from decimal import Decimal

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT


# ─── Paleta de colores ───────────────────────────────────────
COLOR_OSCURO   = colors.HexColor("#1A1720")
COLOR_MEDIO    = colors.HexColor("#2A2733")
COLOR_BORDE    = colors.HexColor("#3D3B3C")
COLOR_ACENTO   = colors.HexColor("#5C5464")
COLOR_TEXTO    = colors.HexColor("#E8E4D9")
COLOR_VERDE    = colors.HexColor("#7EC8A8")
COLOR_ROJO     = colors.HexColor("#C87E7E")
COLOR_AZUL     = colors.HexColor("#7EA8C8")
COLOR_AMBER    = colors.HexColor("#C8A87E")
COLOR_GRIS     = colors.HexColor("#8A8694")


def _header_table(titulo, subtitulo=""):
    """Genera la cabecera del documento bancario."""
    data = [[
        Paragraph(f"<font size='18' color='#E8E4D9'><b>🏦 BancoApp</b></font>", getSampleStyleSheet()["Normal"]),
        Paragraph(f"<font size='11' color='#8A8694'>{titulo}</font><br/>"
                  f"<font size='9' color='#5C5464'>{subtitulo}</font>",
                  getSampleStyleSheet()["Normal"]),
        Paragraph(f"<font size='8' color='#8A8694'>Emitido: {datetime.now().strftime('%d/%m/%Y %H:%M')}</font>",
                  getSampleStyleSheet()["Normal"]),
    ]]
    t = Table(data, colWidths=[6*cm, 9*cm, 5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), COLOR_OSCURO),
        ("TEXTCOLOR",     (0,0), (-1,-1), COLOR_TEXTO),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",         (2,0), (2,0),   "RIGHT"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 14),
        ("TOPPADDING",    (0,0), (-1,-1), 14),
        ("LEFTPADDING",   (0,0), (0,0),   10),
    ]))
    return t


# ─── 1. Estado de cuenta PDF ─────────────────────────────────

def generar_estado_cuenta_pdf(cliente, movimientos) -> bytes:
    """
    Genera el estado de cuenta completo de un cliente en PDF.
    Retorna bytes del PDF.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()
    story = []

    # ── Cabecera ──
    story.append(_header_table("ESTADO DE CUENTA", f"Período: Todos los movimientos"))
    story.append(Spacer(1, 0.4*cm))

    # ── Datos del cliente ──
    info_data = [
        ["Titular",        cliente.nombre,            "N° Cuenta",    cliente.num_cuenta or "—"],
        ["Tipo de cuenta", cliente.tipo.capitalize(),  "Estado",       cliente.estado],
        ["Documento",      f"{cliente.tipo_documento or ''} {cliente.documento or '—'}",
         "Saldo actual",   f"${float(cliente.saldo):,.2f}"],
        ["Email",          cliente.email or "—",       "Teléfono",     cliente.telefono or "—"],
    ]
    info_t = Table(info_data, colWidths=[3.5*cm, 6*cm, 3.5*cm, 6*cm])
    info_t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), COLOR_MEDIO),
        ("BACKGROUND",    (0,0), (0,-1), COLOR_ACENTO),
        ("BACKGROUND",    (2,0), (2,-1), COLOR_ACENTO),
        ("TEXTCOLOR",     (0,0), (-1,-1), COLOR_TEXTO),
        ("FONTSIZE",      (0,0), (-1,-1), 8),
        ("FONTNAME",      (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",      (2,0), (2,-1), "Helvetica-Bold"),
        ("GRID",          (0,0), (-1,-1), 0.5, COLOR_BORDE),
        ("PADDING",       (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(info_t)
    story.append(Spacer(1, 0.5*cm))

    # ── Tabla de movimientos ──
    story.append(Paragraph(
        "<font color='#E8E4D9' size='10'><b>MOVIMIENTOS</b></font>",
        styles["Normal"]
    ))
    story.append(Spacer(1, 0.2*cm))

    if movimientos:
        cab = ["Fecha", "Tipo", "Monto ($)", "Descripción"]
        filas = [cab]

        TIPO_POSITIVO = {"Deposito", "Transferencia Recibida", "Apertura", "Prestamo"}

        for m in movimientos:
            fecha = m.fecha.strftime("%Y-%m-%d %H:%M") if hasattr(m.fecha, "strftime") else str(m.fecha)
            signo = "+" if m.tipo in TIPO_POSITIVO else "-"
            filas.append([
                fecha,
                m.tipo,
                f"{signo}${float(m.monto):,.2f}",
                (m.descripcion or "")[:55],
            ])

        col_w = [3.5*cm, 3.5*cm, 3*cm, 9*cm]
        mov_t = Table(filas, colWidths=col_w, repeatRows=1)

        estilos_celdas = [
            ("BACKGROUND",    (0,0), (-1,0),  COLOR_ACENTO),
            ("TEXTCOLOR",     (0,0), (-1,0),  COLOR_TEXTO),
            ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,0),  8),
            ("ALIGN",         (2,0), (2,-1),  "RIGHT"),
            ("ALIGN",         (0,0), (1,-1),  "LEFT"),
            ("BACKGROUND",    (0,1), (-1,-1), COLOR_MEDIO),
            ("TEXTCOLOR",     (0,1), (-1,-1), COLOR_TEXTO),
            ("FONTSIZE",      (0,1), (-1,-1), 7.5),
            ("GRID",          (0,0), (-1,-1), 0.3, COLOR_BORDE),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [COLOR_MEDIO, colors.HexColor("#252230")]),
            ("PADDING",       (0,0), (-1,-1), 5),
        ]
        # Colorear montos
        for i, m in enumerate(movimientos, start=1):
            col = COLOR_VERDE if m.tipo in TIPO_POSITIVO else COLOR_ROJO
            estilos_celdas.append(("TEXTCOLOR", (2,i), (2,i), col))
            estilos_celdas.append(("FONTNAME",  (2,i), (2,i), "Helvetica-Bold"))

        mov_t.setStyle(TableStyle(estilos_celdas))
        story.append(mov_t)
    else:
        story.append(Paragraph("<font color='#8A8694'>Sin movimientos registrados.</font>", styles["Normal"]))

    story.append(Spacer(1, 0.6*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_BORDE))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"<font size='8' color='#8A8694'>BancoApp · Documento generado el "
        f"{datetime.now().strftime('%d/%m/%Y a las %H:%M:%S')} · Solo para uso interno</font>",
        ParagraphStyle("footer", alignment=TA_CENTER, parent=styles["Normal"])
    ))

    doc.build(story)
    return buffer.getvalue()


# ─── 2. Comprobante de transacción PDF ───────────────────────

def generar_comprobante_pdf(movimiento, cliente) -> bytes:
    """Genera un comprobante individual de una transacción."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(_header_table("COMPROBANTE DE TRANSACCIÓN", "Documento oficial"))
    story.append(Spacer(1, 0.6*cm))

    TIPO_POSITIVO = {"Deposito", "Transferencia Recibida", "Apertura", "Prestamo"}
    color_monto = "#7EC8A8" if movimiento.tipo in TIPO_POSITIVO else "#C87E7E"
    signo       = "+" if movimiento.tipo in TIPO_POSITIVO else "-"

    # Monto grande al centro
    story.append(Paragraph(
        f"<font size='28' color='{color_monto}'><b>{signo}${float(movimiento.monto):,.2f}</b></font>",
        ParagraphStyle("monto_big", alignment=TA_CENTER, parent=styles["Normal"])
    ))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"<font size='12' color='#8A8694'>{movimiento.tipo}</font>",
        ParagraphStyle("tipo_tx", alignment=TA_CENTER, parent=styles["Normal"])
    ))
    story.append(Spacer(1, 0.6*cm))

    detalle_data = [
        ["N° Comprobante", f"TXN-{movimiento.id:08d}"],
        ["Titular",         cliente.nombre],
        ["N° Cuenta",       cliente.num_cuenta or "—"],
        ["Fecha y hora",    movimiento.fecha.strftime("%d/%m/%Y %H:%M:%S") if hasattr(movimiento.fecha, "strftime") else str(movimiento.fecha)],
        ["Descripción",     movimiento.descripcion or "—"],
        ["Estado",          "PROCESADO ✓"],
    ]

    det_t = Table(detalle_data, colWidths=[5*cm, 12*cm])
    det_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (0,-1), COLOR_ACENTO),
        ("BACKGROUND",  (1,0), (1,-1), COLOR_MEDIO),
        ("TEXTCOLOR",   (0,0), (-1,-1), COLOR_TEXTO),
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("GRID",        (0,0), (-1,-1), 0.4, COLOR_BORDE),
        ("PADDING",     (0,0), (-1,-1), 8),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(det_t)
    story.append(Spacer(1, 1*cm))

    story.append(Paragraph(
        f"<font size='7' color='#5C5464'>Conserve este comprobante como respaldo. "
        f"BancoApp — {datetime.now().strftime('%d/%m/%Y %H:%M')}</font>",
        ParagraphStyle("footer", alignment=TA_CENTER, parent=styles["Normal"])
    ))

    doc.build(story)
    return buffer.getvalue()


# ─── 3. Tabla de amortización PDF ────────────────────────────

def generar_amortizacion_pdf(prestamo, cuotas, cliente) -> bytes:
    """Genera la tabla de amortización completa de un préstamo."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.2*cm, rightMargin=1.2*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(_header_table(
        "TABLA DE AMORTIZACIÓN",
        f"Préstamo #{prestamo.id} — {cliente.nombre}"
    ))
    story.append(Spacer(1, 0.4*cm))

    # Resumen del préstamo
    total_pagado = sum(float(c.monto_cuota) for c in cuotas if c.estado == "PAGADA")
    res_data = [
        ["Capital original", f"${float(prestamo.monto):,.2f}",
         "Plazo", f"{prestamo.plazo_meses} meses"],
        ["Interés total",    f"${float(prestamo.interes):,.2f}",
         "Cuota mensual",    f"${float(prestamo.cuota_mensual):,.2f}"],
        ["Saldo pendiente",  f"${float(prestamo.saldo_pendiente):,.2f}",
         "Vencimiento",      str(prestamo.fecha_vencimiento) if prestamo.fecha_vencimiento else "—"],
        ["Estado",           prestamo.estado,
         "Total pagado",     f"${total_pagado:,.2f}"],
    ]
    if prestamo.mora_acumulada and float(prestamo.mora_acumulada) > 0:
        res_data.append(["Mora acumulada", f"${float(prestamo.mora_acumulada):,.2f}", "Días mora", str(prestamo.dias_mora or 0)])

    res_t = Table(res_data, colWidths=[3.5*cm, 5*cm, 3.5*cm, 6.5*cm])
    res_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (0,-1), COLOR_ACENTO),
        ("BACKGROUND",  (2,0), (2,-1), COLOR_ACENTO),
        ("BACKGROUND",  (1,0), (1,-1), COLOR_MEDIO),
        ("BACKGROUND",  (3,0), (3,-1), COLOR_MEDIO),
        ("TEXTCOLOR",   (0,0), (-1,-1), COLOR_TEXTO),
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",    (2,0), (2,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8),
        ("GRID",        (0,0), (-1,-1), 0.4, COLOR_BORDE),
        ("PADDING",     (0,0), (-1,-1), 6),
    ]))
    story.append(res_t)
    story.append(Spacer(1, 0.5*cm))

    story.append(Paragraph(
        "<font color='#E8E4D9' size='10'><b>CUOTAS</b></font>",
        styles["Normal"]
    ))
    story.append(Spacer(1, 0.2*cm))

    ESTADO_COLOR = {"PAGADA": COLOR_VERDE, "PENDIENTE": COLOR_AMBER, "VENCIDA": COLOR_ROJO}

    cab = ["#", "Vencimiento", "Capital ($)", "Interés ($)", "Cuota ($)", "Saldo ($)", "Estado"]
    filas = [cab]
    for c in cuotas:
        filas.append([
            str(c.numero_cuota),
            str(c.fecha_vencimiento) if c.fecha_vencimiento else "—",
            f"${float(c.capital):,.2f}",
            f"${float(c.interes):,.2f}",
            f"${float(c.monto_cuota):,.2f}",
            f"${float(c.saldo_restante):,.2f}",
            c.estado,
        ])

    col_w = [1*cm, 3*cm, 2.8*cm, 2.8*cm, 2.8*cm, 2.8*cm, 2.3*cm]
    cuota_t = Table(filas, colWidths=col_w, repeatRows=1)

    est_base = [
        ("BACKGROUND",    (0,0), (-1,0),  COLOR_ACENTO),
        ("TEXTCOLOR",     (0,0), (-1,0),  COLOR_TEXTO),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0),  8),
        ("BACKGROUND",    (0,1), (-1,-1), COLOR_MEDIO),
        ("TEXTCOLOR",     (0,1), (-1,-1), COLOR_TEXTO),
        ("FONTSIZE",      (0,1), (-1,-1), 7.5),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [COLOR_MEDIO, colors.HexColor("#252230")]),
        ("GRID",          (0,0), (-1,-1), 0.3, COLOR_BORDE),
        ("PADDING",       (0,0), (-1,-1), 4),
    ]
    for i, c in enumerate(cuotas, start=1):
        col = ESTADO_COLOR.get(c.estado, COLOR_GRIS)
        est_base.append(("TEXTCOLOR", (6,i), (6,i), col))
        est_base.append(("FONTNAME",  (6,i), (6,i), "Helvetica-Bold"))

    cuota_t.setStyle(TableStyle(est_base))
    story.append(cuota_t)

    doc.build(story)
    return buffer.getvalue()


# ─── 4. Exportar movimientos a CSV ───────────────────────────

def generar_movimientos_csv(movimientos, cliente=None) -> bytes:
    """Genera un CSV de movimientos para descarga."""
    output = io.StringIO()
    writer = csv.writer(output)

    if cliente:
        writer.writerow([f"Estado de cuenta — {cliente.nombre}"])
        writer.writerow([f"Cuenta: {cliente.num_cuenta}  |  Saldo actual: ${float(cliente.saldo):,.2f}"])
        writer.writerow([f"Exportado: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
        writer.writerow([])

    writer.writerow(["Fecha", "Tipo", "Monto", "Descripción"])
    TIPO_POSITIVO = {"Deposito", "Transferencia Recibida", "Apertura", "Prestamo"}
    for m in movimientos:
        fecha = m.fecha.strftime("%Y-%m-%d %H:%M") if hasattr(m.fecha, "strftime") else str(m.fecha)
        signo = "+" if m.tipo in TIPO_POSITIVO else "-"
        writer.writerow([
            fecha,
            m.tipo,
            f"{signo}{float(m.monto):.2f}",
            m.descripcion or "",
        ])

    return output.getvalue().encode("utf-8-sig")  # BOM para Excel


# ─── 5. Reporte de balance CSV ───────────────────────────────

def generar_balance_csv(cuentas_data: list) -> bytes:
    """
    cuentas_data: lista de dicts con keys: nombre, categoria, saldo
    """
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([f"Balance General — BancoApp"])
    writer.writerow([f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}"])
    writer.writerow([])
    writer.writerow(["Cuenta", "Categoría", "Saldo ($)"])
    for row in cuentas_data:
        writer.writerow([row["nombre"], row["categoria"], f"{row['saldo']:.2f}"])

    return output.getvalue().encode("utf-8-sig")


# ─── 6. Reporte de balance PDF ───────────────────────────────

def generar_balance_pdf(cuentas_data: list, historial_mensual: list = None) -> bytes:
    """Genera un reporte de balance descargable en PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()
    story = []

    story.append(_header_table("REPORTE DE BALANCE GENERAL",
                               datetime.now().strftime("%d/%m/%Y")))
    story.append(Spacer(1, 0.5*cm))

    # Agrupar por categoría
    from collections import defaultdict
    por_cat = defaultdict(list)
    totales = defaultdict(float)
    for r in cuentas_data:
        por_cat[r["categoria"]].append(r)
        totales[r["categoria"]] += r["saldo"]

    for cat in ["ACTIVO", "PASIVO", "PATRIMONIO", "INGRESO"]:
        cuentas = por_cat.get(cat, [])
        if not cuentas:
            continue
        story.append(Paragraph(
            f"<font color='#E8E4D9' size='9'><b>{cat}S</b></font>",
            styles["Normal"]
        ))
        story.append(Spacer(1, 0.1*cm))
        datos = [["Cuenta", "Saldo ($)"]]
        for r in cuentas:
            datos.append([r["nombre"], f"${r['saldo']:,.2f}"])
        datos.append(["TOTAL", f"${totales[cat]:,.2f}"])

        t = Table(datos, colWidths=[13*cm, 5*cm])
        est = [
            ("BACKGROUND",  (0,0), (-1,0),  COLOR_ACENTO),
            ("BACKGROUND",  (0,1), (-1,-2), COLOR_MEDIO),
            ("BACKGROUND",  (0,-1), (-1,-1), colors.HexColor("#3A2F45")),
            ("TEXTCOLOR",   (0,0), (-1,-1), COLOR_TEXTO),
            ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
            ("FONTNAME",    (0,-1), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,-1), 8.5),
            ("ALIGN",       (1,0), (1,-1),  "RIGHT"),
            ("GRID",        (0,0), (-1,-1), 0.3, COLOR_BORDE),
            ("PADDING",     (0,0), (-1,-1), 6),
        ]
        t.setStyle(TableStyle(est))
        story.append(t)
        story.append(Spacer(1, 0.4*cm))

    doc.build(story)
    return buffer.getvalue()
