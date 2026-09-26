"""
Módulo de Auditoría y Auto-Remediación Continua de Drift en AWS LocalStack.

Este script implementa un bucle de conciliación de seguridad (Reconciliation Loop)
en el Plano de Control (Control Plane). Actúa como un agente de cumplimiento que
compara el estado dinámico actual de los Security Groups frente a una especificación
contractual inmutable (Golden State).

Si el script detecta reglas de red que no forman parte de la línea base aprobada
(como aperturas no autorizadas generadas por hotfixes manuales), purga de forma
automática las reglas anómalas mediante llamadas atómicas a la API de EC2,
restableciendo la política de privilegios mínimos sin requerir intervención humana.
"""

# Importa el SDK oficial de AWS para interactuar con las APIs de infraestructura
import boto3

# Instancia un cliente de bajo nivel para los servicios de Amazon EC2
ec2 = boto3.client(
    # Servicio de AWS a gestionar
    "ec2",
    # Redirige el tráfico de gestión hacia el contenedor de LocalStack
    endpoint_url="http://localhost:4566",
    # Región geográfica requerida por la firma criptográfica SigV4
    region_name="us-east-1",
    # Credencial de acceso simulada para el entorno local
    aws_access_key_id="test",
    # Clave secreta simulada para autenticar las peticiones en botocore
    aws_secret_access_key="test",
)

# Definición del estado deseado (IaC Contract): Conjunto inmutable de puertos autorizados
AUTHORIZED_PORTS = {443}


def reconcile_firewall() -> int:
    """
    Audita el Security Group corporativo y revoca cualquier regla fuera de política.

    Obtiene la configuración en vivo del grupo 'production-perimeter-sg' desde la API,
    recorre cada regla de ingreso (Ingress) y verifica si su puerto de destino
    pertenece al conjunto AUTHORIZED_PORTS. Cualquier regla no declarada es eliminada
    inmediatamente mediante la acción RevokeSecurityGroupIngress.

    Returns:
        int: La cantidad total de reglas no autorizadas (drifts) detectadas y remediadas.

    Raises:
        botocore.exceptions.ClientError: Si ocurre un error de comunicación con
            la API de EC2 en LocalStack o fallan los permisos de revocación.
        IndexError: Si el Security Group consultado no existe en el entorno.
    """
    # Imprime el encabezado visual del procedimiento de conciliación
    print("\n--- EJECUTANDO RECONCILIACIÓN DE SEGURIDAD ---")

    # Consulta a la API de EC2 para recuperar la definición actual del Security Group
    sgs = ec2.describe_security_groups(GroupNames=["production-perimeter-sg"])

    # Extrae el diccionario principal del primer Security Group retornado
    sg = sgs["SecurityGroups"][0]

    # Almacena el identificador único del recurso (ej. 'sg-xxxxxxxx')
    sg_id = sg["GroupId"]

    # Obtiene la lista de reglas de entrada existentes; si no hay, inicializa una lista vacía
    current_rules = sg.get("IpPermissions", [])

    # Inicializa el contador de anomalías o desvíos de configuración
    drift_count = 0

    # Itera sobre cada estructura de regla de ingreso presente en el Security Group
    for rule in current_rules:
        # Extrae el puerto de inicio del rango evaluado
        from_port = rule.get("FromPort")

        # Extrae el puerto de fin del rango evaluado
        to_port = rule.get("ToPort")

        # Extrae el protocolo de transporte de la regla (ej. 'tcp', 'udp', '-1')
        protocol = rule.get("IpProtocol")

        # Evalúa si el puerto de destino NO está incluido en el contrato de seguridad
        if from_port not in AUTHORIZED_PORTS:
            # Incrementa el contador de reglas fuera de política
            drift_count += 1

            # Emite una alerta indicando el ID del recurso alterado
            print(f"[PELIGRO] Regla no autorizada detectada en {sg_id}:")

            # Muestra los metadatos de transporte de la regla anómala
            print(f"          Protocolo: {protocol} | Puertos: {from_port}-{to_port}")

            # Itera sobre los rangos de direcciones IP asociados a la regla no autorizada
            for ip_range in rule.get("IpRanges", []):
                # Extrae la notación CIDR del bloque de red autorizado
                cidr = ip_range.get("CidrIp")

                # Comprueba si la apertura concede acceso irrestricto desde Internet
                if cidr == "0.0.0.0/0":
                    # Alerta con severidad crítica sobre la exposición pública total
                    print(f"          -> Rango de red CRÍTICO: {cidr}")

            # Informa el inicio de la remediación activa en caliente
            print("[REMEDIACIÓN] Revocando regla fuera de política mediante API...")

            # Invoca la API RevokeSecurityGroupIngress pasando la estructura exacta de la regla
            ec2.revoke_security_group_ingress(
                # Especifica el recurso sobre el cual aplicar la purga
                GroupId=sg_id,
                # Provee la lista con la regla detectada para eliminarla de la tabla del hipervisor
                IpPermissions=[rule],
            )

            # Confirma que la regla fue eliminada y se restauró la línea base
            print("[OK] Regla revocada. El firewall regresa al Golden State.")

    # Si tras examinar todas las reglas no hubo anomalías, confirma la consistencia
    if drift_count == 0:
        print("[OK] Estado consistente: La infraestructura cumple con el 100% de la política.")

    # Retorna la cantidad de desvíos corregidos para trazabilidad o aserciones en tests
    return drift_count


# Bloque de ejecución principal (Entry Point)
if __name__ == "__main__":
    # Dispara la rutina de reconciliación cuando el script se invoca por línea de comandos
    reconcile_firewall()