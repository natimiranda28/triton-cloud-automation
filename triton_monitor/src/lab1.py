"""
Módulo: triton_cloud_lab.py
Descripción: Provisor de almacenamiento S3 y cómputo EC2 de forma idempotente,
             no bloqueante (mediante ThreadPoolExecutor) e integrado con un
             pipeline de observabilidad estructurado JSON para LocalStack.
UPATECO - Programación para Automatización II (2026)
"""

import os
import sys
import json
import queue
import logging
import logging.config
import logging.handlers
import asyncio
from datetime import datetime, timezone
import boto3
import botocore
from botocore.config import Config
from botocore.exceptions import ClientError, ParamValidationError

# =====================================================================
# 1. PIPELINE DE OBSERVABILIDAD ESTRUCTURADA JSON
# =====================================================================

class CloudJSONFormatter(logging.Formatter):
    """
    Formateador avanzado que captura metadatos forenses de las APIs de AWS 
    (ResponseMetadata, RequestId y HTTPStatusCode) ante fallos de ClientError.
    """
    def format(self, record: logging.LogRecord) -> str:
        dt_utc = datetime.fromtimestamp(record.created, tz=timezone.utc)
        log_payload = {
            "timestamp": dt_utc.isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "async_task": getattr(record, "taskName", "None"),
            "thread": record.threadName,
            "line": record.lineno
        }
        
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            if exc_value:
                log_payload["exception"] = {
                    "class": exc_type.__name__ if exc_type else "Unknown",
                    "message": str(exc_value)
                }
                # Si el error proviene de una llamada fallida a las APIs de AWS (ClientError)
                if isinstance(exc_value, ClientError):
                    response = exc_value.response
                    log_payload["exception"]["aws_metadata"] = {
                        "error_code": response.get("Error", {}).get("Code", "UnknownCode"),
                        "error_message": response.get("Error", {}).get("Message", "UnknownMessage"),
                        "http_status_code": response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0),
                        "request_id": response.get("ResponseMetadata", {}).get("RequestId", "None")
                    }
        return json.dumps(log_payload, ensure_ascii=False)


def setup_observability() -> logging.Logger:
    """Configura e inicia el pipeline de logging asíncrono no bloqueante."""
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_fmt": {"()": CloudJSONFormatter},
            "console_fmt": {"format": "%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s"}
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "formatter": "console_fmt",
                "stream": "ext://sys.stdout"
            },
            "file": {
                "class": "logging.FileHandler",
                "level": "DEBUG",
                "formatter": "json_fmt",
                "filename": "triton_cloud_ops.json.log",
                "encoding": "utf-8"
            }
        },
        "loggers": {
            "triton_cloud": {
                "level": "DEBUG",
                "handlers": ["console", "file"],
                "propagate": False
            }
        }
    }
    logging.config.dictConfig(config)
    app_logger = logging.getLogger("triton_cloud")
    
    # Desacoplamiento síncrono-asíncrono mediante colas en memoria
    log_queue = queue.Queue(-1)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    real_handlers = app_logger.handlers
    
    listener = logging.handlers.QueueListener(log_queue, *real_handlers, respect_handler_level=True)
    app_logger.handlers = [queue_handler]
    listener.start()
    app_logger.listener = listener
    
    return app_logger


logger = setup_observability()


# =====================================================================
#  2. GESTOR DE RECURSOS AWS S3 & EC2 (ClientError-safe)
# =====================================================================

class TritonCloudResourceManager:
    """
    Administrador de recursos de AWS que opera de forma idempotente
    con redirección de endpoints hacia el puerto local de LocalStack.
    """
    def __init__(self, profile_name: str = "triton-local") -> None:
        self.profile = profile_name
        self.region = "us-east-1"
        
        # Redirección a LocalStack local
        self.endpoint_url = "http://127.0.0.1:4566"
        
        # Configuración corporativa de reintentos
        self.aws_config = Config(
            retries={
                "max_attempts": 3,
                "mode": "standard"
            }
        )

    def _get_thread_safe_session(self) -> boto3.Session:
        """
        Instancia una sesión Boto3 aislada por hilo para garantizar
        la atomicidad y seguridad para hilos concurrentes.
        """
        return boto3.Session(
            profile_name=self.profile,
            region_name=self.region
        )

    # -----------------------------------------------------------------
    # SECCIÓN S3 (ALMACENAMIENTO)
    # -----------------------------------------------------------------

    async def provision_idempotent_bucket(self, bucket_name: str, owner: str) -> bool:
        """Aprovisiona un S3 Bucket de forma idempotente con metadata de control (Tagging)."""
        loop = asyncio.get_running_loop()
        session = self._get_thread_safe_session()
        s3_client = session.client("s3", endpoint_url=self.endpoint_url, config=self.aws_config)
        
        logger.info(f"Auditando almacenamiento: Buscando bucket '{bucket_name}' en LocalStack...")
        
        # 1. Auditoría de Idempotencia: head_bucket() para ver si ya existe
        try:
            await loop.run_in_executor(
                None,
                lambda: s3_client.head_bucket(Bucket=bucket_name)
            )
            logger.info(
                f"IDEMPOTENCIA ACTIVADA: El S3 Bucket '{bucket_name}' ya se encuentra nominal y activo. Saltando.",
                extra={"bucket": bucket_name, "idempotent_match": True}
            )
            return False
            
        except ClientError as err:
            status_code = err.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if status_code != 404:
                # Si es un error de conflicto (403) o red, lo re-lanzamos
                raise err
            
        # 2. Creación del recurso S3 al no existir (Fase de Fallback)
        logger.warning(f"RECURSO S3 AUSENTE: Iniciando aprovisionamiento físico para '{bucket_name}'...")
        try:
            await loop.run_in_executor(
                None,
                lambda: s3_client.create_bucket(Bucket=bucket_name)
            )
            
            # Aplicar metadatos de control (Etiquetas S3) en el origen
            await loop.run_in_executor(
                None,
                lambda: s3_client.put_bucket_tagging(
                    Bucket=bucket_name,
                    Tagging={
                        "TagSet": [
                            {"Key": "Owner", "Value": owner},
                            {"Key": "Project", "Value": "Triton-DevOps"},
                            {"Key": "Ambiente", "Value": "TP2-Produccion"}
                        ]
                    }
                )
            )
            logger.info(f" S3 Bucket '{bucket_name}' aprovisionado y etiquetado con éxito.")
            return True
            
        except ClientError as err:
            logger.error(f"Fallo crítico en la API de S3 al aprovisionar '{bucket_name}'", exc_info=True)
            raise

    # -----------------------------------------------------------------
    # SECCIÓN EC2 (CÓMPUTO)
    # -----------------------------------------------------------------

    async def provision_idempotent_instance(self, instance_name: str, owner: str) -> str:
        """Aprovisiona un nodo EC2 de forma idempotente con etiquetado en origen."""
        loop = asyncio.get_running_loop()
        session = self._get_thread_safe_session()
        ec2_client = session.client("ec2", endpoint_url=self.endpoint_url, config=self.aws_config)
        
        logger.info(f"Auditando cómputo: Localizando nodo '{instance_name}' en LocalStack...")
        
        # 1. Auditoría de Idempotencia: Verificar si existe instancia con ese nombre activa
        try:
            response = await loop.run_in_executor(
                None,
                lambda: ec2_client.describe_instances(
                    Filters=[
                        {"Name": "tag:Name", "Values": [instance_name]},
                        {"Name": "instance-state-name", "Values": ["pending", "running"]}
                    ]
                )
            )
            reservations = response.get("Reservations", [])
            if reservations and reservations[0].get("Instances", []):
                instance_id = reservations[0]["Instances"][0]["InstanceId"]
                logger.info(
                    f"IDEMPOTENCIA ACTIVADA: El nodo de cómputo '{instance_name}' ya existe con ID '{instance_id}'. Saltando.",
                    extra={"instance_name": instance_name, "instance_id": instance_id, "idempotent_match": True}
                )
                return instance_id
                
        except ClientError as err:
            logger.error("Error al auditar estado de cómputo EC2 en LocalStack.", exc_info=True)
            raise err

        # 2. Creación con Metadatos de Control en el Origen (TagSpecifications)
        logger.warning(f"RECURSO EC2 AUSENTE: Levantando nodo de cómputo '{instance_name}'...")
        try:
            create_response = await loop.run_in_executor(
                None,
                lambda: ec2_client.run_instances(
                    ImageId="ami-0c55b159cbfafe1f0",  # AMI simulada compatible con LocalStack
                    MinCount=1,
                    MaxCount=1,
                    InstanceType="t3.micro",
                    TagSpecifications=[{
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Name", "Value": instance_name},
                            {"Key": "Owner", "Value": owner},
                            {"Key": "Project", "Value": "Triton-DevOps"},
                            {"Key": "Ambiente", "Value": "TP2-Produccion"}
                        ]
                    }]
                )
            )
            new_id = create_response["Instances"][0]["InstanceId"]
            logger.info(f" Nodo EC2 '{instance_name}' levantado exitosamente con ID '{new_id}'.")
            return new_id
            
        except ClientError as err:
            logger.error(f"Fallo crítico en la API de EC2 al levantar '{instance_name}'", exc_info=True)
            raise err


# =====================================================================
#  3. ORQUESTADOR DE EJECUCIÓN (PEP 765 / Syntax-Warning-safe)
# =====================================================================

async def main_async():
    manager = TritonCloudResourceManager(profile_name="triton-local")
    
    # Parámetros del despliegue corporativo
    bucket_telemetria = "triton-operational-telemetry-bucket"
    nodo_computo = "triton-processing-node-01"
    propietario = "SRE-Team-UPATECO"
    
    try:
        # Aprovisionamiento asíncrono en paralelo de almacenamiento y cómputo
        await asyncio.gather(
            manager.provision_idempotent_bucket(bucket_telemetria, propietario),
            manager.provision_idempotent_instance(nodo_computo, propietario)
        )
        print("\n PIPELINE DE INFRAESTRUCTURA FINALIZADO: Estado real sincronizado con éxito.")
        
    except ParamValidationError as err:
        print(f"\n Error de Validación de Parámetros de Botocore: {err}", file=sys.stderr)
        
    except ClientError as err:
        print(f"\n Error de API de AWS (ClientError): {err.response['Error']['Code']} - {err.response['Error']['Message']}", file=sys.stderr)
        
    except Exception as err:
        print(f"\n Falla de Orquestación Desconocida: {err}", file=sys.stderr)
        
    finally:
        logger.info("Alineación de recursos finalizada de forma determinista.")
        await asyncio.sleep(0.5)
        logger.listener.stop()


if __name__ == "__main__":
    asyncio.run(main_async())