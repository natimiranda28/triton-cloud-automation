import argparse
import asyncio
import logging
import sys

from triton_telemetry import (
    setup_triton_logging,
    scan_all_providers,
    parse_timeout,
    parse_cluster_id,
    ProviderTimeoutError,
    NetworkPeeringError,
    CorruptedPayloadError,
    TritonError


)

# Inicio el pipeline declarativo de logging estructurado asíncrono
logger = setup_triton_logging() 

def build_cli_parser() -> argparse.ArgumentParser:
    

    parser = argparse.ArgumentParser(
        prog="TritonMonitor",
        description=(
            "Consola de Telemetría Multicloud y "
            "Observabilidad Asíncrona (PROYECTO TRITÓN)."
        ),
    )

    # Proveedores
    parser.add_argument(
        "proveedores",
        nargs="+",
        choices=["AWS", "Azure", "GCP"],
        help="Lista de proveedores cloud a monitorear.",
    )

    # Identificador del clúster
    parser.add_argument(
        "-c",
        "--cluster-id",
        type=parse_cluster_id,
        required=True,
        help=(
            "Identificador del clúster "
            "(formato: cluster-<region>-<numero_dos_digitos>)."
        ),
    )

    # Timeout
    parser.add_argument(
        "-t",
        "--timeout",
        type=parse_timeout,
        default=2.5,
        help="Tiempo de espera para las peticiones HTTP (0.1s - 5.0s).",
    )

    # Modo Chaos
    parser.add_argument(
        "--chaos",
        action="store_true",
        help="Forzar inyección de fallos reales para pruebas.",
    )

    # Modo de operación
    parser.add_argument(
        "-m",
        "--mode",
        choices=["nominal", "debug", "emergency"],
        default="nominal",
        help="Modo de operación del despachador de telemetría.",
    )

    return parser


async def async_main():
    """
    Punto principal asíncrono de TritonMonitor.

    Procesa los argumentos, ejecuta el monitoreo concurrente
    y captura de forma independiente los errores del dominio.
    """

    parser = build_cli_parser()
    args = parser.parse_args()

    # Información inicial de la ejecución
    logger.info("=" * 64)
    logger.info(" INICIANDO MONITOREO MULTICLOUD: PROYECTO TRITÓN")
    logger.info("=" * 64)

    logger.info(f" Clúster Objetivo: {args.cluster_id}")
    logger.info(f" Modo Operativo: {args.mode.upper()}")
    logger.info(
        f" Proveedores seleccionados: {', '.join(args.proveedores)}"
    )
    logger.info(f" Timeout límite configurado: {args.timeout}s")

    if args.chaos:
        logger.warning(
            " ADVERTENCIA: MODO CAOS ACTIVADO. "
            "Se inyectarán fallos reales de red."
        )

    logger.info("=" * 64)

    try:
        # Ejecuta el monitoreo concurrente definido en core.py
        results = await scan_all_providers(
            args.proveedores,
            args.timeout,
            chaos=args.chaos,
        )

        # Si todas las consultas terminan correctamente
        logger.info(
            "\n ESCANEO COMPLETADO CON ÉXITO SIN ANOMALÍAS:"
        )

        for result in results:
            logger.info(
                f" • {result['provider']} -> "
                f"Latencia de Red: {result['latency_sec']:.3f}s | "
                f"ID de Evento: {result['payload_id']} | "
                f"Estado: {result['status']}"
            )

    except* ProviderTimeoutError as group:
        """
        Captura exclusivamente los errores de timeout
        que fueron agrupados por asyncio.TaskGroup.
        """

        logger.error(
            f"\n ANOMALÍA: DETECTADOS TIMEOUTS EN "
            f"PROVEEDORES CLOUD "
            f"({len(group.exceptions)} incidentes):"
        )

        for exc in group.exceptions:
            logger.error(f" Fallo: {exc}")

            # Mostrar información agregada mediante add_note()
            for note in getattr(exc, "__notes__", []):
                logger.error(
                    f" └─ [FORENSE TRITÓN] {note}"
                )

    except* NetworkPeeringError as group:
        """
        Captura exclusivamente los errores relacionados
        con red, DNS, conexión o routing.
        """

        logger.error(
            f"\n ANOMALÍA: DETECTADOS FALLOS DE "
            f"CONEXIÓN O ROUTING "
            f"({len(group.exceptions)} incidentes):"
        )

        for exc in group.exceptions:
            logger.error(f" Fallo: {exc}")

            for note in getattr(exc, "__notes__", []):
                logger.error(
                    f" └─ [FORENSE TRITÓN] {note}"
                )

    except* CorruptedPayloadError as group:
        """
        Captura exclusivamente las respuestas que no
        pudieron interpretarse correctamente.
        """

        logger.error(
            f"\n ADVERTENCIA: RECIBIDOS PAYLOADS "
            f"DE TELEMETRÍA CORRUPTOS "
            f"({len(group.exceptions)} incidentes):"
        )

        for exc in group.exceptions:
            logger.error(f" Fallo: {exc}")

            for note in getattr(exc, "__notes__", []):
                logger.error(
                    f" └─ [FORENSE TRITÓN] {note}"
                )

    except* TritonError as group:
        """
        Captura otros errores generales pertenecientes
        al dominio de TritonMonitor.
        """

        logger.error(
            "\n DETECTADO ERROR OPERACIONAL "
            "EN EL ECOSISTEMA TRITÓN:"
        )

        for exc in group.exceptions:
            logger.error(f" Fallo: {exc}")

    finally:
        # PEP 765 
        logger.info("\n" + "=" * 64)
        logger.info("  [FIN DE CICLO] Recursos liberados de la Operación Tritón.")
        logger.info("=" * 64)
        
       
        listener = getattr(logger, "listener", None)
        if listener:
            listener.stop()


if __name__ == "__main__":
    asyncio.run(async_main())

