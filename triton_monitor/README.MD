# TP-1: Sistema de Telemetría Multicloud y Observabilidad Asíncrona (Proyecto Tritón)

## Descripción General del Proyecto
**Proyecto Tritón** es un monitor CLI de infraestructura cloud diseñado para evaluar en tiempo real la salud y el rendimiento de clústeres distribuidos en múltiples proveedores (**AWS, Azure y GCP**). El sistema implementa comunicación HTTP asíncrona, orquestación concurrente, manejo quirúrgico de excepciones con grupos (`ExceptionGroup` y `except*`), enriquecimiento forense con `add_note()`, y un pipeline de logging desacoplado y no bloqueante con rotación y compresión Gzip.

## Contribución Individual - Integrante 1: Ingeniero de Robustez de Entradas y Excepciones

Como **Integrante 1**, asumí la responsabilidad de construir la capa base de seguridad, validación de parámetros y el diseño del sistema de excepciones semánticas del proyecto, ubicados en los módulos `src/triton_telemetry/exceptions.py` y `src/triton_telemetry/sanitizer.py`.

A continuación, detallo en profundidad cada uno de los componentes y decisiones técnicas que desarrollé:

---

### 1. Diseño de Excepciones Semánticas (`exceptions.py`)

Para evitar el uso de errores genéricos y proporcionar un sistema de "alarmas" precisas para el dominio de telemetría, diseñé una jerarquía de excepciones personalizadas:

* **Regla de Herencia Estricta (`TritonError`):** Creé la clase base `TritonError` heredando obligatoriamente de la clase estándar `Exception` y nunca de `BaseException`. Esta decisión arquitectónica es crítica para evitar capturar señales de sistema; garantiza que el programa no secuestre eventos vitales del sistema operativo, permitiendo al usuario forzar el cierre de la terminal con el comando `Ctrl+C` si la aplicación se congela.


* **Subclases de Dominio:** Implementé tres excepciones específicas para tipificar los fallos de red con precisión quirúrgica:


* `ProviderTimeoutError`: Lanzada cuando un proveedor de nube supera el tiempo de espera de red establecido.


* `CorruptedPayloadError`: Lanzada ante respuestas con formato corrupto o códigos de estatus HTTP fallidos.


* `NetworkPeeringError`: Lanzada para identificar fallos de DNS o problemas de resolución de hosts.

### 2. Validadores de Entrada CLI (`sanitizer.py`)

Actuando como un filtro inicial antes de que el programa procese los datos, desarrollé validadores estrictos para limpiar los argumentos que el usuario ingresa por la terminal:

* **Control del Tiempo de Espera (`parse_timeout`):** Codifiqué una función *callable* que restringe el parámetro `--timeout` exclusivamente a un rango flotante de **0.1 a 5.0 segundos**.


* **Salida Limpia del Sistema:** Si el usuario inyecta datos fuera de rango o de un tipo no numérico, el validador intercepta el error y lanza un `argparse.ArgumentTypeError`. Esto evita que la aplicación colapse con un *traceback* desordenado y fuerza a la CLI a salir limpiamente devolviendo un **código de error de sistema 2** (estándar en administración de sistemas).


* **Validador de Identificadores de Clúster (`parse_cluster_id`):** Desarrollé un validador mediante expresiones regulares utilizando el módulo `re`. Este mecanismo verifica que el parámetro de clúster opcional siga de forma estricta el patrón `cluster-<region>-<numero>` (por ejemplo, `cluster-us-east-01`), rechazando automáticamente cualquier formato inválido.
---

## Contribución Individual - Integrante 2: Ingeniero de Concurrencia y Telemetría Asíncrona

Como **Integrante 2**, asumí la responsabilidad del diseño, implementación y validación del motor central de red y concurrencia del proyecto, ubicado en el módulo [`src/triton_telemetry/core.py`](file:///c:/Users/ivang/OneDrive/Documentos/TP1-PROG2/src/triton_telemetry/core.py), su integración en [`src/triton_telemetry/__init__.py`](file:///c:/Users/ivang/OneDrive/Documentos/TP1-PROG2/src/triton_telemetry/__init__.py) y su suite de verificación automatizada en [`tests/test_core.py`](file:///c:/Users/ivang/OneDrive/Documentos/TP1-PROG2/tests/test_core.py).

A continuación, detallo en profundidad cada uno de los componentes y decisiones técnicas que desarrollé:

---

### 1. Consumo Asíncrono de APIs Reales (`httpx.AsyncClient`)
Para evitar simulaciones locales y apegarme a un escenario de producción real, implementé la corrutina `query_provider_telemetry(...)` utilizando la librería no bloqueante `httpx`.

* **Endpoints Nominales:** Configuré endpoints reales para verificar el estado de los tres proveedores cloud:
  * **AWS Status:** `https://jsonplaceholder.typicode.com/posts/1`
  * **Azure Status:** `https://jsonplaceholder.typicode.com/posts/2`
  * **GCP Status:** `https://jsonplaceholder.typicode.com/posts/3`
* **Métricas y Telemetría:**
  * Medí con alta precisión la latencia de respuesta en milisegundos empleando `time.perf_counter()`.
  * Extraje el código de estado HTTP (`response.status_code`), el cuerpo JSON deserializado (`payload`) y clasifiqué el estado operativo como `HEALTHY`.

---

### 2. Inyección de Caos y Manejo Forense de Errores de Red
Diseñé un sistema de tolerancia a fallos y resiliencia que captura excepciones nativas de transporte y las transforma en excepciones de dominio semánticas (diseñadas por el Integrante 1), garantizando **encadenamiento explícito (`raise ... from exc`)** para no perder el traceback original y **enriquecimiento forense con `add_note()`** (estándar moderno de Python 3.11+):

1. **Gatillo de Timeout Real (`ProviderTimeoutError`):**
   * Configuré el endpoint de retardo controlado `https://httpbin.org/delay/3` (3 segundos de delay).
   * Cuando se ejecuta con un timeout inferior (ej. `--timeout 1.0` o `0.5s`), `httpx` dispara de forma real un `httpx.TimeoutException`.
   * Capturé esta excepción, adjunté notas forenses (`exc.add_note(...)`) con el proveedor, tiempo límite y endpoint objetivo, y relancé la excepción como `ProviderTimeoutError` encadenada a la causa raíz.
2. **Gatillo de Estatus HTTP Erróneos (`CorruptedPayloadError`):**
   * Utilicé `response.raise_for_status()` para detectar respuestas HTTP no exitosas (códigos 4xx y 5xx).
   * Configuré endpoints de caos como `https://httpbin.org/status/504` (Gateway Timeout) y `https://httpbin.org/status/422` (Unprocessable Entity).
   * Al recibir un `httpx.HTTPStatusError`, adjunté una nota forense con el extracto de la respuesta del servidor y re-lancé un `CorruptedPayloadError` preservando la causa original con `from exc`.
3. **Fallo de Peering / DNS (`NetworkPeeringError`):**
   * Capturé errores de resolución de nombres y fallos de conexión (`httpx.ConnectError`, `httpx.NetworkError`), adjunté notas forenses descriptivas y los transformé en `NetworkPeeringError`.
4. **Captura Defensiva General (`TritonError`):**
   * Envolví cualquier otro error inesperado en `TritonError` con contexto forense para evitar fallos catastróficos no controlados.

---

### 3. Orquestación Concurrente con `asyncio.TaskGroup`
Implementé la función principal `scan_all_providers(...)` para orquestar la consulta simultánea de todos los proveedores configurados:

* **Gestor de Contexto Asíncrono:** Utilicé `async with httpx.AsyncClient() as client` y `async with asyncio.TaskGroup() as tg`.
* **Despacho de Tareas:** Creé dinámicamente las tareas con `tg.create_task(...)`, asignándoles nombres identificatorios (`name=f"task-telemetry-{provider.lower()}"`) para facilitar su trazabilidad y serialización en los logs estructurados (aprovechando `taskName` en Python 3.12+).
* **Propagación Quirúrgica:** Si una o varias tareas fallan, `TaskGroup` agrupa automáticamente todas las excepciones en un `ExceptionGroup` que viaja intacto hacia `app_operator.py` para su captura quirúrgica mediante bloques `except*`.

---

### 4. Empaquetado y Suite de Pruebas Automatizadas
* **Exposición en API Pública:** Actualicé [`src/triton_telemetry/__init__.py`](file:///c:/Users/ivang/OneDrive/Documentos/TP1-PROG2/src/triton_telemetry/__init__.py) exportando `query_provider_telemetry`, `scan_all_providers`, `NOMINAL_ENDPOINTS` y `CHAOS_ENDPOINTS` en `__all__`.
* **Suite de Pruebas (`tests/test_core.py`):**
  Desarrollé una suite de pruebas asíncrona automatizada que valida individualmente:
  1. Captura de `NetworkPeeringError` ante dominios inexistentes.
  2. Captura de `ProviderTimeoutError` con endpoint de delay y verificación de notas forenses (`__notes__`).
  3. Captura de `CorruptedPayloadError` ante respuestas HTTP 504.
  4. Escaneo concurrente exitoso de AWS, Azure y GCP en modo nominal con verificación de latencias y payloads.

---

## Ejecución de Pruebas

Para ejecutar las pruebas del módulo `core.py`:

```bash
python tests/test_core.py
```

---

## Contribución Individual - Integrante 4: Ingeniero de Almacenamiento y Desacoplamiento No Bloqueante

Como **Integrante 4**, asumí la responsabilidad del diseño e implementación del pipeline de persistencia de logs, ubicado en el módulo [`src/triton_telemetry/logging_engine.py`], específicamente la porción de desacoplamiento no bloqueante, rotación y compresión (compartiendo archivo con el formateador JSON del Integrante 3).

A continuación, detallo en profundidad cada uno de los componentes y decisiones técnicas que desarrollé:

---

### 1. Pipeline No Bloqueante (`QueueHandler` + `QueueListener`)

El sistema ejecuta sus consultas HTTP sobre un único hilo (el *event loop* de `asyncio`), por lo que cualquier escritura sincrónica a disco bloquearía la totalidad de las corrutinas de red en curso. Para resolverlo:

* Implementé `ForensicQueueHandler`, un `QueueHandler` que corre en el hilo principal y encola cada `LogRecord` de forma instantánea, sin realizar ninguna escritura física.
* Configuré un `QueueListener` que corre en un **hilo secundario dedicado**, consumiendo la cola en loop y despachando recién ahí los registros a los *handlers* reales (consola y archivo).
* Utilicé `queue.Queue`, una estructura *thread-safe*, como canal de comunicación entre ambos hilos, evitando condiciones de carrera.

### 2. Manejador Rotativo Acotado (`RotatingFileHandler`)

Configuré el handler de archivo con `maxBytes=2*1024*1024` (2 MB) y `backupCount=3`, garantizando que el log activo rote automáticamente al alcanzar el límite de tamaño y que el historial de respaldos nunca supere las 3 copias, descartando la más antigua.

### 3. Compresión Gzip en Caliente

Implementé los callbacks `gzip_namer` y `gzip_rotator`, inyectados sobre el `RotatingFileHandler` para interceptar el ciclo de rotación (*rollover*):
* `gzip_namer` define que el archivo rotado finalice en `.gz`.
* `gzip_rotator` comprime el contenido con la librería nativa `gzip` y elimina el archivo plano residual únicamente después de confirmar la escritura exitosa del comprimido, evitando pérdida de datos ante fallos intermedios.

### 4. Hallazgo Técnico: Preservación de `exc_info` en la Cola

Durante la validación detecté que `QueueHandler.prepare()`, en su implementación estándar, descarta `record.exc_info` antes de encolar — comportamiento pensado para colas que cruzan procesos (`multiprocessing.Queue`), donde un *traceback* no es serializable. Como en Tritón la cola opera dentro de un mismo proceso, ese descarte no aplica y le rompía al Integrante 3 la posibilidad de construir el árbol recursivo de excepciones en el `QueueListener`. Sobreescribí `prepare()` en `ForensicQueueHandler` para que el registro llegue intacto al otro lado de la cola.

### 5. Ensamblado del Pipeline (`wire_non_blocking_pipeline`)

Centralicé las tres responsabilidades anteriores en una única función, `wire_non_blocking_pipeline(app_logger)`, que recibe un logger ya configurado (con sus *handlers* reales definidos vía `dictConfig`) y lo envuelve: inyecta los callbacks de compresión, arma la cola y el `QueueListener`, y expone un único *handler* no bloqueante hacia el resto de la aplicación. Su ciclo de vida se cierra desde `app_operator.py`, en el bloque `finally`, mediante `listener.stop()`, garantizando el drenaje completo de la cola antes de terminar el proceso.

---

### Integrante 5: Coordinador de Integración y Flujo CLI (app_operator.py)

Responsable de la unificación del sistema de telemetría, el diseño de la interfaz de consola de comandos, el inicio seguro del motor de logging declarativo y la gestión de la concurrencia asíncrona de red ante fallas múltiples.

####  Responsabilidades e Implementaciones Clave:
* **Punto de Entrada CLI Oficial (app_operator.py)**: Configuración de un analizador declarativo con argparse que inyecta directamente como tipos de datos los validadores del Integrante 1 (parse_cluster_id y parse_timeout). Esto asegura una validación en la frontera del sistema antes de iniciar el bucle de eventos. Cuenta con restricciones estrictas de dominio (choices) para los modos operativos (nominal, debug, emergency) y banderas opcionales de simulación de caos (--chaos).
* **Configuración Declarativa con dictConfig**: Diseño e inyección estructurada del esquema de configuración del pipeline de logging al arrancar la aplicación.
* **Captura Quirúrgica mediante except* (Python 3.11+)**: Implementación de la orquestación resiliente ante fallas simultáneas de red de los proveedores HTTP, procesando quirúrgicamente el ExceptionGroup propagado por el TaskGroup del Integrante 2:
    * except* ProviderTimeoutError as group: Captura los tiempos de espera agotados de los proveedores cloud de la API real y extrae dinámicamente las notas forenses inyectadas con .add_note().
    * except* NetworkPeeringError as group: Mitiga de manera segura pérdidas físicas de ruteo, DNS o transporte de red.
    * except* CorruptedPayloadError as group: Maneja formatos no serializables en JSON u otros errores lógicos sin tumbar la ejecución de la consola.
    * except* TritonError as group: Atrapa errores genéricos del ecosistema de telemetría no catalogados.
* **Cumplimiento de Estándares de Producción (PEP 765 / Python 3.14)**: Estructuración del bloque de limpieza finally para asegurar el apagado ordenado del listener de hilos del logger. Se eliminó estrictamente cualquier uso de directivas de control de flujo (return, break o continue) en finally para evitar silenciamientos ciegos de excepciones activas y la generación de advertencias del compilador.


## Contribución Individual - Integrante 3: Ingeniero de Formateo Estructurado JSON

Como **Integrante 3**, asumí la responsabilidad del diseño e implementación del formateador de logs estructurados en JSON, ubicado en el módulo [`src/triton_telemetry/logging_engine.py`], específicamente la clase `AsyncJSONFormatter`.

A continuación, detallo en profundidad cada uno de los componentes y decisiones técnicas que desarrollé:

---

### 1. Formateador JSON Forense (`AsyncJSONFormatter`)
Diseñé `AsyncJSONFormatter` heredando de `logging.Formatter` y sobreescribiendo su método `format(self, record)`, respetando el contrato implícito del módulo `logging` (recibir un `LogRecord`, devolver un `str`). Esto permite que el resto del pipeline de logging (handlers, listeners) trate mi formateador exactamente igual que cualquier `Formatter` estándar, sin necesidad de conocer la lógica JSON interna.

* **Metadatos de telemetría dinámica capturados en cada evento:**
  * Marca de tiempo estricta en formato **ISO 8601 UTC** (usando `datetime.fromtimestamp(record.created, tz=timezone.utc)`), garantizando coherencia horaria independientemente del huso del servidor.
  * `process` (PID del sistema operativo) y `threadName` (identificador del hilo).
  * `taskName`, nativo de `asyncio` en Python 3.12+, para distinguir a qué tarea concurrente (AWS, Azure o GCP) pertenece cada evento cuando corren en simultáneo dentro de un `TaskGroup`.
  * Metadatos dinámicos inyectados vía el parámetro `extra` de cualquier llamada a `logger`, capturados iterando `record.__dict__` y filtrando los atributos reservados internos de `LogRecord`.

---

### 2. Serialización Recursiva de Excepciones (`_serialize_exception`)
Implementé un extractor recursivo capaz de reconstruir la jerarquía completa de un incidente, sin truncar ninguna excepción residual del grupo:

1. **Encadenamiento explícito (`raise ... from`):** recorro `exc.__cause__` recursivamente para reconstruir la cadena completa de causalidad entre la excepción semántica de dominio (`ProviderTimeoutError`, `CorruptedPayloadError`, `NetworkPeeringError`) y la excepción nativa de `httpx` que la originó.
2. **`ExceptionGroup` anidados (Python 3.11+):** cuando múltiples proveedores fallan en simultáneo dentro de un `asyncio.TaskGroup`, recorro `exc.exceptions` y serializo cada sub-excepción de forma independiente y recursiva, preservando la estructura jerárquica completa en un nodo `nested_exceptions`.
3. **Notas forenses dinámicas (`add_note()`):** extraigo `__notes__` de cada excepción (PEP 678), sin asumir su existencia previa mediante `getattr` con valor por defecto.
4. **Datos reales de las APIs de `httpx` que colapsaron:** mediante despacho por tipo con `isinstance` contra la jerarquía de excepciones de `httpx` (`HTTPStatusError` vs. `RequestError`), rescato la URL, el método HTTP y el código de estado real de la petición que falló, dejándolos en un nodo `httpx_details` indexable.

---

### 3. Integración con el Pipeline No Bloqueante
Coordiné con el Integrante 4 la integración de mi formateador dentro del pipeline de `QueueHandler`/`QueueListener`, verificando que el `exc_info` de cada `LogRecord` llegue intacto hasta mi formateador.

---
