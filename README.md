# MixAI

Base inicial para el analizador de mezclas de ATPtorres. Este corte permite medir una mezcla real y comparar reportes en un piloto de uso local. El contrato PostgreSQL describe la capa SaaS prevista.

## Probar el motor

Requisitos del entorno de desarrollo: Python 3.12, FFmpeg y FFprobe en PATH. Los filtros de FFmpeg deben incluir `ebur128` con true peak y `ametadata`. Instala las dependencias en un entorno virtual.

```bash
cd services/api
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m mixai /ruta/mezcla.wav
python -m unittest discover -s tests -v
```

En CMD de Windows, activa con `.venv\Scripts\activate.bat`. El comando del analizador admite rutas con espacios entre comillas.

La salida JSON incluye LUFS integrado, LUFS short-term cada segundo tras la primera ventana de 3 s, true peak, pico de muestra, RMS, crest factor, LRA, correlación, ancho, pérdida mono, bandas, espectro y alertas limitadas de señal. El límite es 80 MiB, 0,5–300 s, mono o estéreo y 44,1–96 kHz. Los archivos se detectan por contenedor y códec. Las mediciones sin valor válido se devuelven como `null`.

## Ejecutar el piloto local

Inicia la API desde `services/api` con el entorno virtual activo.

```bash
python -m uvicorn mixai.http:app --host 127.0.0.1 --port 8000 --workers 1 --limit-concurrency 32
```

En otra terminal, desde la raíz del ZIP, con Node.js 22 LTS:

```bash
cd apps/web
npm install
npm run dev
```

Abre [127.0.0.1:3000](http://127.0.0.1:3000). Para levantar solo la API en Docker, ejecuta `docker compose -f infra/compose.yaml up --build` desde la raíz. Los reportes se guardan en SQLite, en `./state` o en el directorio configurado por `MIXAI_STATE`. El piloto guarda las mediciones y elimina sus copias temporales de audio al terminar. Solo procesa dos cargas simultáneas por proceso.

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/healthz` | Proceso vivo |
| POST | `/v1/local/reports` | Reporte nuevo, código 201 |
| GET | `/v1/local/reports` | Hasta 50 reportes recientes |
| GET | `/v1/local/reports/{id}` | Un reporte |
| DELETE | `/v1/local/reports/{id}` | Borrado local, código 204 |

La carga HTTP usa el archivo binario como cuerpo con `Content-Type: application/octet-stream`.

```bash
curl -fsS -H 'Content-Type: application/octet-stream' --data-binary @mezcla.wav \
  http://127.0.0.1:8000/v1/local/reports
```

## Estructura

```text
apps/web/                  Interfaz del piloto con Next.js, TypeScript y Tailwind
services/api/mixai/         CLI, motor DSP y API FastAPI
services/api/tests/         Pruebas con señales sintéticas y formatos reales
db/migrations/001_init.sql  DDL inicial de PostgreSQL para la arquitectura SaaS
infra/compose.yaml         API del piloto con contenedor local y volumen SQLite
```

## Paso a producción

Mantén el piloto en loopback. El despliegue público necesita Clerk y verificación de roles, almacenamiento privado y URLs temporales S3, cola SQS con worker e idempotencia, migraciones PostgreSQL y roles RLS, límites por workspace, retención y pruebas de restauración. Aplica la migración en una base de pruebas con `psql -v ON_ERROR_STOP=1 -f db/migrations/001_init.sql`. Completa los permisos para roles, relay y worker en una migración de infraestructura antes del uso multiusuario.

En el siguiente commit, genera y versiona el lockfile npm, fija dependencias transitivas Python e imágenes por digest, activa CI de web/API/SQL, ejecuta las muestras EBU y añade pruebas de carga, permisos y fallos de cola. Revisa la licencia del build de FFmpeg que se distribuya. Las referencias de género, scoring y modelos de IA requieren corpus autorizado y validación. El PDF incluye el contrato de API de producción, los esquemas y el orden de implementación.

## Verificación de este corte

Siete pruebas del motor completadas con Python, NumPy, SciPy y FFmpeg 6.1.1. Se verificaron WAV, MP3, FLAC, seno estéreo de referencia, silencio, contrafase, picos débiles y finales de archivo, audio inválido y duración mínima. La instalación y ejecución integral de Next.js y FastAPI, la migración en PostgreSQL y el contenedor quedan por validar en el entorno del proyecto.
