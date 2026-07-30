"""
Pipeline de violencia de género (Saiku → CSV con combinatoria completa).

Se ejecuta SOLO cuando el monitor (vg_monitor_victimas.py) detecta un
cambio en el contador público de víctimas. Reconstruye la consulta que
haría un usuario manualmente en el portal Saiku (sesión anónima → crear
consulta → añadir Año/Mes/Relación/Convivencia/Denuncia a filas →
indicador a columnas → pedir resultado plano), y aplica la misma lógica
de procesamiento (combinatoria completa, subtotales condicionados,
márgenes y gran total) que el script original en Colab.
"""

import os
import re
import time
import uuid
import smtplib
import ssl
import unicodedata
import difflib
from urllib.parse import quote
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from dotenv import load_dotenv
import requests
import pandas as pd

load_dotenv()  # en local, carga variables desde .env si existe; en GitHub Actions no hace nada (no hay archivo)

BASE = "https://estadisticasviolenciagenero.igualdad.gob.es/saiku/rest/saiku"
CATALOG = "VDG_CIUDADANO_PRO"
CUBE = "010 Feminicidios en la pareja o expareja"
MEDIDA = "Feminicidios pareja o expareja"

DIMENSIONES = [
    {
        "nombre": "02 Estructura temporal - Año",
        "hierarchy": "[02 Estructura temporal - Año].[Año]",
        "level": "[02 Estructura temporal - Año].[Año].[Año]",
    },
    {
        "nombre": "03 Estructura temporal - Mes",
        "hierarchy": "[03 Estructura temporal - Mes].[Mes]",
        "level": "[03 Estructura temporal - Mes].[Mes].[Mes]",
    },
    {
        "nombre": "11 Relación y convivencia - Relación VM y AG",
        "hierarchy": "[11 Relación y convivencia - Relación VM y AG].[VM-AG Relación]",
        "level": "[11 Relación y convivencia - Relación VM y AG].[VM-AG Relación].[VM-AG Relación]",
    },
    {
        "nombre": "13 Relación y convivencia - Convivencia VM y AG",
        "hierarchy": "[13 Relación y convivencia - Convivencia VM y AG].[VM-AG Conviviencia]",
        "level": "[13 Relación y convivencia - Convivencia VM y AG].[VM-AG Conviviencia].[VM-AG Conviviencia]",
    },
    {
        "nombre": "14 Tutela - Tipo constancia - Denuncia -2-",
        "hierarchy": "[14 Tutela - Tipo constancia - Denuncia -2-].[Denuncia -2-]",
        "level": "[14 Tutela - Tipo constancia - Denuncia -2-].[Denuncia -2-].[Denuncia -2-]",
    },
]

ARCHIVO_SALIDA = "ViolenciaGeneroOK.csv"

DESTINATARIOS = [
    "inakihernandez@europapress.es",
    "yonrecio@europapress.es",
]

HEADERS_BASE = {"Accept": "application/json, text/javascript, */*; q=0.01"}


# ---------------------------------------------------------------------------
# Cliente Saiku: replica la secuencia de peticiones que hace el navegador
# ---------------------------------------------------------------------------

def _post_con_diagnostico(session, url, data, timeout=30):
    resp = session.post(url, data=data, timeout=timeout)
    if not resp.ok:
        print(f"  >>> Error {resp.status_code} en POST {url}")
        print(f"  >>> Cuerpo de la respuesta: {resp.text[:1000]!r}")
    resp.raise_for_status()
    return resp


def _get_con_diagnostico(session, url, timeout=30):
    resp = session.get(url, timeout=timeout)
    if not resp.ok:
        print(f"  >>> Error {resp.status_code} en GET {url}")
        print(f"  >>> Cuerpo de la respuesta: {resp.text[:1000]!r}")
    resp.raise_for_status()
    return resp


def crear_sesion():
    session = requests.Session()
    session.headers.update(HEADERS_BASE)
    ts = int(time.time() * 1000)
    resp = _get_con_diagnostico(session, f"{BASE}/session?_={ts}")
    return session


def crear_query(session):
    query_id = str(uuid.uuid4()).upper()
    url = f"{BASE}/anonymousUser/query/{query_id}"
    data = {
        "connection": "xmla",
        "catalog": CATALOG,
        "schema": "",
        "cube": CUBE,
        "formatter": "flat",
        "type": "QM",
    }
    resp = _post_con_diagnostico(session, url, data)
    print(f"  >>> Respuesta al crear la consulta: {resp.text[:500]!r}")
    return query_id


def anadir_dimension_a_filas(session, query_id, dim, position):
    url = (
        f"{BASE}/anonymousUser/query/{query_id}/axis/ROWS/dimension/"
        f"{quote(dim['nombre'], safe='')}/hierarchy/"
        f"{quote(dim['hierarchy'], safe='')}/"
        f"{quote(dim['level'], safe='')}"
    )
    _post_con_diagnostico(session, url, {"position": position})


def anadir_medida(session, query_id, position=0):
    measure_uniquename = f"[Measures].[{MEDIDA}]"
    url = (
        f"{BASE}/anonymousUser/query/{query_id}/axis/COLUMNS/dimension/Measures/member/"
        f"{quote(measure_uniquename, safe='')}"
    )
    _post_con_diagnostico(session, url, {"position": position})


def obtener_resultado(session, query_id):
    ts = int(time.time() * 1000)
    url = f"{BASE}/anonymousUser/query/{query_id}/result/flat?limit=0&_={ts}"
    resp = _get_con_diagnostico(session, url, timeout=60)
    return resp.json()


def cellset_a_dataframe(payload):
    filas = payload["cellset"]
    if not filas:
        raise ValueError("La respuesta de Saiku no contiene filas (cellset vacío).")

    nombres_columnas = [celda["value"] for celda in filas[0]]
    registros = [[celda["value"] for celda in fila] for fila in filas[1:]]

    return pd.DataFrame(registros, columns=nombres_columnas)


def obtener_datos_saiku():
    session = crear_sesion()
    query_id = crear_query(session)
    for posicion, dim in enumerate(DIMENSIONES):
        anadir_dimension_a_filas(session, query_id, dim, posicion)
    anadir_medida(session, query_id)
    payload = obtener_resultado(session, query_id)
    return cellset_a_dataframe(payload)


# ---------------------------------------------------------------------------
# Procesamiento: misma lógica que el cuaderno original (Colab)
# ---------------------------------------------------------------------------

def norm_col(s: str) -> str:
    s = (s or "").replace("\ufeff", "").strip().strip('"').strip("'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[\s/]+", ".", s)
    s = re.sub(r"[^0-9A-Za-z_.]+", ".", s)
    s = re.sub(r"\.+", ".", s).strip(".")
    return s.lower()


def norm_val(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def replace_by_norm(series: pd.Series, rules):
    s = series.astype(str)
    normed = s.map(norm_val)
    for rx, dest in rules:
        mask = normed.str.match(rx)
        s = s.where(~mask, dest)
        normed = s.map(norm_val)
    return s


TARGETS = {
    "VM.AG.Relación": [
        "VM.AG.Relacion", "VM AG Relacion", "VM.AG Relacion", "VM_AG_Relacion",
        "Relacion", "VM AG Relación", "VM.AG.Relación", "VM-AG Relación",
    ],
    "VM.AG.Conviviencia": [
        "VM.AG.Convivencia", "VM AG Convivencia", "VM_AG_Convivencia",
        "Convivencia", "VM.AG.Conviviencia", "VM AG Conviviencia", "VM-AG Conviviencia",
    ],
    "Denuncia": ["Denuncia", "Denuncia -2-"],
    "Feminicidios.pareja.o.expareja": [
        "Feminicidios pareja o expareja", "Feminicidios_pareja_o_expareja",
        "Feminicidios.pareja.expareja", "Feminicidios en pareja o expareja",
        "Feminicidios.pareja.o.expareja",
    ],
}


def procesar_violencia(df_raw: pd.DataFrame) -> pd.DataFrame:
    violencia = df_raw.copy()

    # --- Renombrar columnas a los nombres estándar
    idx = {norm_col(c): c for c in violencia.columns}
    ren = {}
    for target, candidatos in TARGETS.items():
        target_norm = norm_col(target)
        found = None
        for cand in candidatos:
            if norm_col(cand) in idx:
                found = idx[norm_col(cand)]
                break
        if not found:
            close = difflib.get_close_matches(target_norm, list(idx.keys()), n=1, cutoff=0.8)
            if close:
                found = idx[close[0]]
        if found and found != target:
            ren[found] = target
    if ren:
        violencia = violencia.rename(columns=ren)

    # --- Normalizar valores de las dimensiones
    if "VM.AG.Relación" in violencia.columns:
        violencia["VM.AG.Relación"] = replace_by_norm(
            violencia["VM.AG.Relación"],
            [
                (r"^ex?conyuge(\s*/\s*|.*)expareja$", "Expareja"),
                (r"^conyuge$", "Pareja"),
            ],
        )

    if "VM.AG.Conviviencia" in violencia.columns:
        violencia["VM.AG.Conviviencia"] = replace_by_norm(
            violencia["VM.AG.Conviviencia"],
            [
                (r"^si\s*conviv\.?$", "Sí"),
                (r"^no\s*conviv\.?$", "No"),
                (r"^no\s*consta$", "No contesta"),
            ],
        )

    if "Denuncia" in violencia.columns:
        violencia["Denuncia"] = violencia["Denuncia"].where(violencia["Denuncia"] != "No", "No había denuncia")
        violencia["Denuncia"] = violencia["Denuncia"].where(violencia["Denuncia"] != "No consta", "No consta denuncia")
        violencia["Denuncia"] = violencia["Denuncia"].str.replace("Sí", "Había denuncia", regex=False)

    # --- Parámetros
    valor = "Feminicidios.pareja.o.expareja"
    claves = ["Año", "Mes"]
    dims = ["VM.AG.Relación", "VM.AG.Conviviencia", "Denuncia"]

    faltan = [c for c in (claves + dims + [valor]) if c not in violencia.columns]
    if faltan:
        raise KeyError(f"Faltan columnas tras el renombrado: {faltan}. Columnas disponibles: {list(violencia.columns)}")

    for d in dims:
        violencia[d] = violencia[d].astype(str)

    base = violencia[~(
        (violencia["VM.AG.Relación"] == "Total")
        | (violencia["VM.AG.Conviviencia"] == "Total")
        | (violencia["Denuncia"] == "Total")
    )].copy()
    base[valor] = pd.to_numeric(base[valor], errors="coerce").fillna(0)

    # --- Detalle con combinatoria completa por mes
    rels = sorted(base["VM.AG.Relación"].dropna().unique().tolist())
    convs = sorted(base["VM.AG.Conviviencia"].dropna().unique().tolist())
    denu = sorted(base["Denuncia"].dropna().unique().tolist())

    detalle_real = (
        base.groupby(claves + dims, dropna=False, as_index=False)[valor]
        .sum()
        .rename(columns={valor: "Victimas"})
    )

    detalles = []
    for (anio, mes), g in detalle_real.groupby(["Año", "Mes"], dropna=False):
        idx_all = pd.MultiIndex.from_product(
            [[anio], [mes], rels, convs, denu],
            names=["Año", "Mes", "VM.AG.Relación", "VM.AG.Conviviencia", "Denuncia"],
        )
        g2 = (
            g.set_index(["Año", "Mes", "VM.AG.Relación", "VM.AG.Conviviencia", "Denuncia"])
            .reindex(idx_all, fill_value=0)
            .reset_index()
        )
        detalles.append(g2)

    detalle = pd.concat(detalles, ignore_index=True)
    detalle["tipo"] = "detalle"

    # --- Subtotales condicionados
    def subtotal_dim(df, dim_name):
        otras = [d for d in dims if d != dim_name]
        tmp = df.groupby(claves + otras, dropna=False, as_index=False)["Victimas"].sum()
        tmp[dim_name] = "Total"
        tmp["tipo"] = f"subtotal_{dim_name}"
        return tmp[claves + dims + ["Victimas", "tipo"]]

    subtotales = pd.concat([subtotal_dim(detalle, d) for d in dims], ignore_index=True)

    # --- Márgenes por dimensión
    def margen_dim(df, dim_name):
        otras = [d for d in dims if d != dim_name]
        tmp = df.groupby(claves + [dim_name], dropna=False, as_index=False)["Victimas"].sum()
        for od in otras:
            tmp[od] = "Total"
        tmp["tipo"] = f"margen_{dim_name}"
        return tmp[claves + dims + ["Victimas", "tipo"]]

    margenes = pd.concat([margen_dim(detalle, d) for d in dims], ignore_index=True)

    # --- Gran total
    gran_total = detalle.groupby(claves, dropna=False, as_index=False)["Victimas"].sum()
    for d in dims:
        gran_total[d] = "Total"
    gran_total["tipo"] = "gran_total"
    gran_total = gran_total[claves + dims + ["Victimas", "tipo"]]

    # --- Ensamble final
    final = pd.concat([detalle, subtotales, margenes, gran_total], ignore_index=True)
    final[valor] = final.apply(lambda r: r["Victimas"] if r["tipo"] == "detalle" else 0, axis=1)

    df_con_totales = final.copy()
    df_con_totales["Territorio"] = "España"
    df_con_totales["EdadVictima"] = "Total"
    df_con_totales["EdadAgresor"] = "Total"

    df_con_totales = df_con_totales[[
        "Territorio", "Año", "Mes", "Victimas",
        "EdadVictima", "EdadAgresor",
        "VM.AG.Relación", "VM.AG.Conviviencia", "Denuncia",
    ]]

    map_meses = {
        "Enero": "1", "Febrero": "2", "Marzo": "3", "Abril": "4", "Mayo": "5", "Junio": "6",
        "Julio": "7", "Agosto": "8", "Septiembre": "9", "Octubre": "10", "Noviembre": "11", "Diciembre": "12",
    }
    df_con_totales["Mes"] = df_con_totales["Mes"].replace(map_meses)

    return df_con_totales


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

ORDEN_MESES = {
    nombre: i for i, nombre in enumerate(
        ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
        start=1,
    )
}


def resumen_ultimo_mes(df_raw: pd.DataFrame):
    df = df_raw.copy()
    df["_anio_num"] = pd.to_numeric(df["Año"], errors="coerce")
    df["_mes_num"] = df["Mes"].map(ORDEN_MESES)
    df["_valor"] = pd.to_numeric(df["Feminicidios pareja o expareja"], errors="coerce").fillna(0)
    df = df.dropna(subset=["_anio_num", "_mes_num"])

    ultimo = df.sort_values(["_anio_num", "_mes_num"]).iloc[-1]
    anio_ultimo, mes_ultimo = int(ultimo["_anio_num"]), ultimo["Mes"]

    df_mes = df[(df["_anio_num"] == anio_ultimo) & (df["Mes"] == mes_ultimo)]
    total_mes = df_mes["_valor"].sum()
    por_relacion = df_mes.groupby("VM-AG Relación")["_valor"].sum().sort_values(ascending=False)
    por_convivencia = df_mes.groupby("VM-AG Conviviencia")["_valor"].sum().sort_values(ascending=False)

    return anio_ultimo, mes_ultimo, total_mes, por_relacion, por_convivencia


def enviar_email(asunto, cuerpo, adjunto=None):
    remitente = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]

    msg = MIMEMultipart()
    msg["From"] = remitente
    msg["To"] = ", ".join(DESTINATARIOS)
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    if adjunto and os.path.exists(adjunto):
        with open(adjunto, "rb") as f:
            parte = MIMEBase("application", "octet-stream")
            parte.set_payload(f.read())
        encoders.encode_base64(parte)
        parte.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(adjunto)}"',
        )
        msg.attach(parte)

    contexto = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=contexto)
        server.login(remitente, password)
        server.sendmail(remitente, DESTINATARIOS, msg.as_string())


def main():
    contador_anterior = os.environ.get("VG_CONTADOR_ANTERIOR")
    contador_actual = os.environ.get("VG_CONTADOR_ACTUAL")

    print("Descargando datos completos desde Saiku...")
    df_raw = obtener_datos_saiku()
    print(f"  {len(df_raw)} filas recibidas.")

    print("Procesando (combinatoria, subtotales, márgenes)...")
    df_final = procesar_violencia(df_raw)
    df_final.to_csv(ARCHIVO_SALIDA, index=False, encoding="utf-8")
    print(f"✓ Generado {ARCHIVO_SALIDA} con {len(df_final)} filas.")

    anio_ultimo, mes_ultimo, total_mes, por_relacion, por_convivencia = resumen_ultimo_mes(df_raw)

    asunto = f"Actualización víctimas VG — datos hasta {mes_ultimo} {anio_ultimo}"
    partes = [
        "Se ha detectado un cambio en el contador público de víctimas y se ha "
        "regenerado el desglose completo desde Saiku.\n",
    ]
    if contador_anterior and contador_actual:
        partes.append(f"\nContador público: {contador_anterior} → {contador_actual}\n")

    partes.append(
        f"\nÚltimo mes con datos en el desglose: {mes_ultimo} {anio_ultimo}\n"
        f"Total feminicidios pareja/expareja ese mes: {total_mes:.0f}\n"
    )
    partes.append("\nPor tipo de relación:\n")
    for k, v in por_relacion.items():
        partes.append(f"  {k}: {v:.0f}\n")
    partes.append("\nPor convivencia:\n")
    for k, v in por_convivencia.items():
        partes.append(f"  {k}: {v:.0f}\n")
    partes.append(
        f"\nEl CSV completo (histórico con combinatoria, subtotales y márgenes) "
        f"se ha actualizado en el repo: {ARCHIVO_SALIDA}\n"
    )

    enviar_email(asunto, "".join(partes), adjunto=ARCHIVO_SALIDA)
    print("✓ Email enviado (con el CSV adjunto).")


if __name__ == "__main__":
    main()