# tests/test_core.py
import asyncio
import sys
import os
from pathlib import Path

# Agregar src al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from triton_telemetry import (
    scan_all_providers,
    query_provider_telemetry,
    ProviderTimeoutError,
    CorruptedPayloadError,
    NetworkPeeringError,
    TritonError,
)


async def run_suite():
    print("==================================================")
    print(" TRITON MONITOR - TEST SUITE: INTEGRANTE 2 (core.py)")
    print("==================================================")

    # 1. Prueba Peering / DNS
    print("\n[TEST 1] Fallo de Peering / DNS...")
    try:
        await scan_all_providers(
            ["AWS"],
            custom_endpoints={"AWS": "https://nonexistent-subdomain-12345.test"},
        )
        print("[ERROR] Se esperaba NetworkPeeringError")
    except* NetworkPeeringError as eg:
        print("[OK] NetworkPeeringError capturado correctamente.")
        for exc in eg.exceptions:
            print(f"   - Mensaje: {exc}")
            print(f"   - Causa raiz: {type(exc.__cause__).__name__}")
            print(f"   - Notas forenses: {exc.__notes__}")

    # 2. Prueba Timeout Real
    print("\n[TEST 2] Fallo de Timeout Real (delay 3s con timeout 0.5s)...")
    try:
        await scan_all_providers(
            ["AWS"],
            timeout=0.5,
            custom_endpoints={"AWS": "https://httpbin.org/delay/3"},
        )
        print("[ERROR] Se esperaba ProviderTimeoutError")
    except* ProviderTimeoutError as eg:
        print("[OK] ProviderTimeoutError capturado correctamente.")
        for exc in eg.exceptions:
            print(f"   - Mensaje: {exc}")
            print(f"   - Causa raiz: {type(exc.__cause__).__name__}")
            print(f"   - Notas forenses: {exc.__notes__}")

    # 3. Prueba HTTP Status 504 (CorruptedPayloadError)
    print("\n[TEST 3] Fallo de Estatus HTTP 504 (Gateway Timeout)...")
    try:
        await scan_all_providers(
            ["Azure"],
            custom_endpoints={"Azure": "https://httpbin.org/status/504"},
        )
        print("[ERROR] Se esperaba CorruptedPayloadError")
    except* CorruptedPayloadError as eg:
        print("[OK] CorruptedPayloadError capturado correctamente.")
        for exc in eg.exceptions:
            print(f"   - Mensaje: {exc}")
            print(f"   - Causa raiz: {type(exc.__cause__).__name__}")
            print(f"   - Notas forenses: {exc.__notes__}")

    # 4. Prueba Nominal Multicloud Concurrente
    print("\n[TEST 4] Telemetria Nominal Concurrente (AWS, Azure, GCP)...")
    try:
        results = await scan_all_providers(["AWS", "Azure", "GCP"], timeout=4.0)
        print(f"[OK] Telemetria nominal completada para {len(results)} proveedores:")
        for r in results:
            print(f"   - Proveedor: {r['provider']} | Estado: {r['status']} | Latencia: {r['latency_ms']} ms | HTTP: {r['status_code']}")
    except Exception as exc:
        print(f"[ERROR] {exc}")

    print("\n==================================================")
    print(" TODAS LAS PRUEBAS DE CORE.PY COMPLETADAS CON EXITO ")
    print("==================================================")


if __name__ == "__main__":
    asyncio.run(run_suite())
