import gzip
import json
import logging
import logging.config
import logging.handlers
import os
import queue
import shutil
from datetime import datetime, timezone
from typing import Any, Dict
import httpx

#  Callbacks de compresión en caliente para el RotatingFileHandler

def gzip_namer(name: str) -> str:
    """Modifica el nombre del archivo de backup agregando la extensión .gz."""
    return name + ".gz"

def gzip_rotator(source: str, dest: str) -> None:

    with open(source, "rb") as f_in:
        with gzip.open(dest, "wb", compresslevel=9) as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(source)

# Formateador JSON forense

class AsyncJSONFormatter(logging.Formatter):

    def _serialize_exception(self, exc: BaseException) -> Dict[str, Any]:
        # Estructura base de la excepción capturada
        exc_data: Dict[str, Any] = {
            "class": exc.__class__.__name__,
            "message": str(exc),
            "notes": getattr(exc, "__notes__", [])
        }

        # Datos reales de la API httpx que colapsó: si la excepción (en
        # cualquier nivel de la recursión) es del "árbol genealógico" de
        # httpx, rescatamos la URL, el método y el código de estado real.
        if isinstance(exc, httpx.HTTPStatusError):
            # La petición llegó al servidor, pero la respuesta fue un error
            # (4xx/5xx). httpx guarda automáticamente request y response.
            exc_data["httpx_details"] = {
                "url": str(exc.request.url),
                "method": exc.request.method,
                "status_code": exc.response.status_code,
            }
        elif isinstance(exc, httpx.RequestError):
            # La petición ni siquiera llegó a tener respuesta (timeout,
            # DNS caído, sin conexión). Cubre también TimeoutException,
            # ConnectError, etc., porque todos heredan de RequestError.
            exc_data["httpx_details"] = {
                "url": str(exc.request.url) if exc.request else None,
                "method": exc.request.method if exc.request else None,
            }

        # Soporte para ExceptionGroup (Python 3.11+): recorrido de múltiples errores concurrentes
        if isinstance(exc, ExceptionGroup):
            exc_data["nested_exceptions"] = [
                self._serialize_exception(nested_err)
                for nested_err in exc.exceptions
            ]
        # Soporte para encadenamiento explícito de errores ('raise ... from')
        elif exc.__cause__:
            exc_data["cause"] = self._serialize_exception(exc.__cause__)

        return exc_data

    def format(self, record: logging.LogRecord) -> str:
        # Generación de marca de tiempo estricta en formato ISO 8601 UTC (terminada en 'Z')
        dt_utc = datetime.fromtimestamp(record.created, tz=timezone.utc)

        # Payload principal estructurado en formato JSON con metadatos del sistema y tareas async
        log_payload: Dict[str, Any] = {
            "timestamp": dt_utc.isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": record.process,
            "async_task": getattr(record, "taskName", "None"),
            "thread_name": record.threadName,
            "filename": record.filename,
            "line": record.lineno
        }

        # Integración del árbol de excepciones si el registro contiene información de error
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            if exc_value:
                log_payload["exception_tree"] = self._serialize_exception(exc_value)
            # Volcado completo del stack trace tradicional para diagnóstico
            log_payload["stack_trace"] = self.formatException(record.exc_info)

        # Listado de campos internos de Python para prevenir duplicaciones en el JSON
        reserved_fields = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "taskName"
        }

        # Inyección dinámica de metadatos personalizados provistos mediante el parámetro 'extra'
        for key, value in record.__dict__.items():
            if key not in reserved_fields and not key.startswith('_'):
                log_payload[key] = value

        # Serialización final del diccionario a una cadena de texto JSON válida
        return json.dumps(log_payload, ensure_ascii=False)


# QueueHandler que preserva el exc_info real
class ForensicQueueHandler(logging.handlers.QueueHandler):
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


def wire_non_blocking_pipeline(
    app_logger: logging.Logger,
) -> logging.handlers.QueueListener:
    
    # 1) Compresión Gzip en caliente sobre el handler rotativo, si existe.
    file_handler = next(
        (h for h in app_logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)),
        None,
    )
    if file_handler is not None:
        file_handler.namer = gzip_namer
        file_handler.rotator = gzip_rotator

    # 2) Desacoplamiento no bloqueante: QueueHandler (productor, corre en el
    #    hilo/loop principal) + QueueListener (consumidor, hilo secundario).
    log_queue: "queue.Queue" = queue.Queue(-1)
    queue_handler = ForensicQueueHandler(log_queue)

    real_handlers = app_logger.handlers
    listener = logging.handlers.QueueListener(
        log_queue, *real_handlers, respect_handler_level=True
    )

    # A partir de aquí, el logger solo encola instantáneamente en memoria;
    # la escritura física queda delegada por completo al hilo del listener.
    app_logger.handlers = [queue_handler]

    listener.start()
    app_logger.listener = listener  # type: ignore[attr-defined]

    return listener

def setup_triton_logging(log_filename: str = "triton_services.log") -> logging.Logger:
    
    logging_schema = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_structured": {"()": AsyncJSONFormatter},
            "console_clean": {
                "format": "%(asctime)s [%(levelname)s] (%(taskName)s) %(message)s",
                "datefmt": "%H:%M:%S",
            },
        },
        "handlers": {
            "stdout_console": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "formatter": "console_clean",
                "stream": "ext://sys.stdout",
            },
            "rotating_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "DEBUG",
                "formatter": "json_structured",
                "filename": log_filename,
                "maxBytes": 2 * 1024 * 1024,  # 2 MB por archivo
                "backupCount": 3,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "triton_monitor": {
                "level": "DEBUG",
                "handlers": ["stdout_console", "rotating_file"],
                "propagate": False,
            }
        },
    }

    logging.config.dictConfig(logging_schema)
    app_logger = logging.getLogger("triton_monitor")

    wire_non_blocking_pipeline(app_logger)

    return app_logger