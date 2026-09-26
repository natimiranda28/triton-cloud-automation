# src/triton_telemetry/core.py
import asyncio
import time
from typing import Any, Dict, List, Optional
import httpx

from .exceptions import (
    CorruptedPayloadError,
    NetworkPeeringError,
    ProviderTimeoutError,
    TritonError,
)

# Endpoints nominales para telemetría real
NOMINAL_ENDPOINTS: Dict[str, str] = {
    "AWS": "https://jsonplaceholder.typicode.com/posts/1",
    "Azure": "https://jsonplaceholder.typicode.com/posts/2",
    "GCP": "https://jsonplaceholder.typicode.com/posts/3",
}

# Endpoints de inyección de caos y fallos de resiliencia
CHAOS_ENDPOINTS: Dict[str, str] = {
    "AWS": "https://httpbin.org/delay/3",        # Dispara ProviderTimeoutError si timeout < 3.0s
    "Azure": "https://httpbin.org/status/504",   # Dispara CorruptedPayloadError (Gateway Timeout)
    "GCP": "https://httpbin.org/status/422",     # Dispara CorruptedPayloadError (Unprocessable Entity)
}


async def query_provider_telemetry(
    provider: str,
    client: httpx.AsyncClient,
    timeout: float = 2.5,
    chaos: bool = False,
    custom_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Consulta de forma asíncrona el estado de telemetría de un proveedor cloud.

    :param provider: Nombre del proveedor ('AWS', 'Azure', 'GCP')
    :param client: Instancia activa de httpx.AsyncClient
    :param timeout: Tiempo máximo de espera en segundos
    :param chaos: Si es True, utiliza endpoints de prueba de fallos de red
    :param custom_url: URL personalizada para sobreescribir el endpoint
    :return: Diccionario con la telemetría recopilada (latencia, status, datos)
    :raises ProviderTimeoutError: Si la petición excede el tiempo límite
    :raises CorruptedPayloadError: Si el servidor responde con un código de error HTTP (4xx, 5xx)
    :raises NetworkPeeringError: Si ocurre un error de DNS o fallo de conexión
    """
    if custom_url:
        target_url = custom_url
    elif chaos:
        target_url = CHAOS_ENDPOINTS.get(provider, NOMINAL_ENDPOINTS[provider])
    else:
        target_url = NOMINAL_ENDPOINTS.get(
            provider,
            f"https://jsonplaceholder.typicode.com/posts/{abs(hash(provider)) % 10 + 1}"
        )

    start_time = time.perf_counter()

    try:
        response = await client.get(target_url, timeout=timeout)
        # Verifica códigos de estado HTTP y lanza HTTPStatusError si es 4xx/5xx
        response.raise_for_status()
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        payload = response.json()

        return {
            "provider": provider,
            "status": "HEALTHY",
            "status_code": response.status_code,
            "latency_ms": latency,
            "endpoint": target_url,
            "payload": payload,
        }

    except httpx.TimeoutException as exc:
        forensic_note = f"Timeout superado en el nodo de telemetría de respaldo ({provider}) tras {timeout}s"
        exc.add_note(forensic_note)
        exc.add_note(f"Endpoint objetivo: {target_url}")
        
        custom_err = ProviderTimeoutError(
            f"Fallo de timeout en proveedor '{provider}' tras {timeout}s"
        )
        custom_err.add_note(forensic_note)
        custom_err.add_note(f"Endpoint objetivo: {target_url}")
        raise custom_err from exc

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        forensic_note = f"Estatus HTTP no esperado ({status_code}) recibido desde el proveedor {provider}"
        exc.add_note(forensic_note)
        exc.add_note(f"Respuesta del servidor: {exc.response.text[:200]}")
        
        custom_err = CorruptedPayloadError(
            f"Estatus HTTP no esperado ({status_code}) recibido de '{provider}'"
        )
        custom_err.add_note(forensic_note)
        custom_err.add_note(f"Endpoint objetivo: {target_url}")
        raise custom_err from exc

    except (httpx.ConnectError, httpx.NetworkError) as exc:
        forensic_note = f"Fallo crítico de resolución de peering / DNS hacia '{target_url}'"
        exc.add_note(forensic_note)
        
        custom_err = NetworkPeeringError(
            f"Error de peering o resolución de red con '{provider}'"
        )
        custom_err.add_note(forensic_note)
        custom_err.add_note(f"Endpoint objetivo: {target_url}")
        raise custom_err from exc

    except Exception as exc:
        if isinstance(exc, TritonError):
            raise
        forensic_note = f"Fallo inesperado durante la telemetría de {provider}"
        exc.add_note(forensic_note)
        
        custom_err = TritonError(
            f"Error desconocido en telemetría de '{provider}': {exc}"
        )
        custom_err.add_note(forensic_note)
        raise custom_err from exc


async def scan_all_providers(
    providers: List[str],
    timeout: float = 2.5,
    chaos: bool = False,
    custom_endpoints: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Orquesta la consulta concurrente de telemetría de múltiples proveedores
    utilizando asyncio.TaskGroup.

    Si una o más tareas fallan, asyncio.TaskGroup propaga un ExceptionGroup con
    la totalidad de las excepciones capturadas de forma quirúrgica.

    :param providers: Lista de proveedores a consultar (ej: ['AWS', 'Azure', 'GCP'])
    :param timeout: Tiempo máximo de espera por petición en segundos
    :param chaos: Activa endpoints de inyección de caos
    :param custom_endpoints: Diccionario opcional de endpoints para testing
    :return: Lista de resultados de telemetría para los proveedores exitosos
    """
    results: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        async with asyncio.TaskGroup() as tg:
            tasks = []
            for provider in providers:
                url = custom_endpoints.get(provider) if custom_endpoints else None
                task = tg.create_task(
                    query_provider_telemetry(
                        provider=provider,
                        client=client,
                        timeout=timeout,
                        chaos=chaos,
                        custom_url=url,
                    ),
                    name=f"task-telemetry-{provider.lower()}",
                )
                tasks.append(task)

        # Si todas las tareas tuvieron éxito (sin excepciones en TaskGroup)
        for task in tasks:
            results.append(task.result())

    return results
