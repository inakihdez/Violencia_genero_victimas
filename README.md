# Monitor de víctimas VG + pipeline Saiku

## Archivos

- `vg_monitor_victimas.py` — comprueba el contador público de víctimas y detecta cambios.
- `vg_saiku_pipeline.py` — descarga el desglose completo desde Saiku, aplica combinatoria/subtotales/márgenes, genera `ViolenciaGeneroOK.csv` y envía el email.
- `.github/workflows/vg_monitor.yml` — workflow que ejecuta ambos scripts 3 veces al día en GitHub Actions.

## Probar en local (antes de subir a GitHub)

1. Crea un entorno virtual e instala dependencias:
   ```
   python -m venv venv
   source venv/bin/activate      # en Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Copia `.env.example` como `.env` y rellena tus claves reales:
   ```
   cp .env.example .env
   ```
   Edita `.env` con tu cuenta de Gmail y contraseña de aplicación (sin espacios).

3. **Probar el monitor** (comprueba el contador, no manda email ni ejecuta el pipeline):
   ```
   python vg_monitor_victimas.py
   ```
   - La primera vez, solo guarda el contador en `vg_monitor_estado.json` (esto es normal, no hay "anterior" con quién comparar).
   - Si lo vuelves a ejecutar sin que el contador real haya cambiado, dirá "Sin cambios".
   - Para forzar que detecte un "cambio" y probar el pipeline completo, edita a mano `vg_monitor_estado.json` y baja el número en 1 antes de volver a ejecutar.

4. **Probar el pipeline de Saiku de forma aislada** (sin depender del monitor):
   ```
   python vg_saiku_pipeline.py
   ```
   Esto descarga los datos de Saiku, genera `ViolenciaGeneroOK.csv` en la carpeta actual, y **si `EMAIL_USER`/`EMAIL_PASS` están en tu `.env`, envía el email real** — ten esto en cuenta para no mandar correos de prueba de más a tu compañero mientras pruebas.

5. Revisa `ViolenciaGeneroOK.csv` generado — compáralo con el `ViolenciaGeneroOK.csv` de referencia para confirmar que el formato coincide.

## Subir a GitHub

1. Crea el repo nuevo y sube todos los archivos **excepto `.env`** (ya está en `.gitignore`, no se subirá por accidente).
2. En GitHub, ve a Settings → Secrets and variables → Actions y crea:
   - `EMAIL_USER`
   - `EMAIL_PASS`
3. Lanza el workflow manualmente una vez desde la pestaña Actions (`workflow_dispatch`) para la primera comprobación de referencia.

## Notas

- `vg_monitor_estado.json` y `ViolenciaGeneroOK.csv` se comitean automáticamente al repo desde el workflow — es la forma en que el estado persiste entre ejecuciones. No los excluyas del control de versiones.
- El pipeline de Saiku no se ha podido probar en vivo durante su construcción (sin acceso de red al dominio desde el entorno de desarrollo), así que la primera ejecución real en local es importante para detectar cualquier ajuste necesario en los nombres de dimensión/jerarquía.
