"""
Monitor del contador de víctimas mortales por violencia de género.

Vigila la página pública del Ministerio de Igualdad y compara la cifra
destacada ("X víctimas mortales por Violencia de Género desde el 1 de
enero de 2003 hasta el día de hoy") contra el último valor guardado. Si
cambia (normalmente sube en 1, por un caso nuevo), envía un email de
aviso. Pensado para ejecutarse varias veces al día vía GitHub Actions.
"""

import os
import re
import json
import time

from dotenv import load_dotenv
import requests

load_dotenv()  # en local, carga variables desde .env si existe; en GitHub Actions no hace nada (no hay archivo)

URL = "https://violenciagenero.igualdad.gob.es/violenciaEnCifras/victimasMortales/fichaMujeres/"
ARCHIVO_ESTADO = "vg_monitor_estado.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

PATRON_CONTADOR = re.compile(
    r"([\d\.]+)\s*v[ií]ctimas mortales por Violencia de G[ée]nero",
    re.IGNORECASE,
)


def obtener_total_victimas(intentos=4):
    ultima_excepcion = None
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding

            match = PATRON_CONTADOR.search(resp.text)
            if not match:
                raise RuntimeError(
                    "No se ha encontrado el contador de víctimas en la página. "
                    "Puede que hayan cambiado el texto o el formato."
                )

            numero_str = match.group(1).replace(".", "")
            return int(numero_str)

        except (requests.exceptions.RequestException, RuntimeError) as e:
            ultima_excepcion = e
            print(f"  Intento {intento} fallido: {e}")
            time.sleep(5)

    raise RuntimeError(f"No se pudo obtener el contador tras {intentos} intentos.") from ultima_excepcion


def cargar_estado():
    if os.path.exists(ARCHIVO_ESTADO):
        with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"ultimo_total": None}


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def escribir_output(nombre, valor):
    """Escribe una variable de salida para que el workflow pueda leerla en pasos siguientes."""
    ruta_output = os.environ.get("GITHUB_OUTPUT")
    if ruta_output:
        with open(ruta_output, "a", encoding="utf-8") as f:
            f.write(f"{nombre}={valor}\n")
    else:
        print(f"  [sin GITHUB_OUTPUT disponible] {nombre}={valor}")


def main():
    estado = cargar_estado()
    anterior = estado.get("ultimo_total")

    print(f"Consultando {URL}...")
    actual = obtener_total_victimas()
    print(f"  Total actual: {actual}")

    if anterior is None:
        print(f"Primera ejecución: se guarda el total actual ({actual}) como referencia, sin disparar el pipeline.")
        estado["ultimo_total"] = actual
        guardar_estado(estado)
        escribir_output("cambio", "false")
        return

    if actual == anterior:
        print(f"Sin cambios respecto a la última comprobación ({anterior}).")
        escribir_output("cambio", "false")
        return

    diferencia = actual - anterior
    print(f"Cambio detectado: {anterior} -> {actual} ({diferencia:+d}). Se disparará el pipeline de Saiku.")

    escribir_output("cambio", "true")
    escribir_output("anterior", anterior)
    escribir_output("actual", actual)

    estado["ultimo_total"] = actual
    guardar_estado(estado)


if __name__ == "__main__":
    main()
