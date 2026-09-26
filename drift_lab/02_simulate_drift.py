"""
Módulo de Simulación de Configuration Drift en AWS LocalStack.

Este script reproduce una mutación fuera de banda (Out-of-Band Change) en el
Plano de Control (Control Plane). Modifica directamente en caliente un Security
Group gestionado previamente por Infraestructura como Código (IaC), abriendo
el puerto 8080/TCP hacia todo Internet (0.0.0.0/0) sin registrar el cambio en
repositorios de control de versiones ni canalizaciones de CI/CD.

Dicha alteración simula un "hotfix" no autorizado o una mala praxis operativa,
generando una divergencia crítica entre el estado deseado (Golden State) y el
estado real en ejecución.
"""

# Importa el SDK oficial de AWS para Python
import boto3

# Instancia un cliente de bajo nivel para el servicio EC2
ec2 = boto3.client(
    # Especifica el servicio de cómputo y redes a controlar
    "ec2",
    # Redirige las llamadas REST al contenedor Docker de LocalStack
    endpoint_url="http://localhost:4566",
    # Fija la región geográfica requerida para firmas criptográficas y ARNs
    region_name="us-east-1",
    # Credencial de acceso simulada para el entorno de emulación
    aws_access_key_id="test",
    # Clave secreta requerida por botocore para procesar el handshake SigV4
    aws_secret_access_key="test",
)


def inject_unauthorized_drift() -> str:
    """
    Inyecta una regla no autorizada en el Security Group corporativo.

    Consulta la API de EC2 para resolver el identificador único del grupo
    'production-perimeter-sg' y le agrega una regla de ingreso (Ingress)
    permisiva para el puerto 8080/TCP desde cualquier origen (0.0.0.0/0),
    rompiendo el principio de privilegios mínimos.

    Returns:
        str: El identificador único (GroupId) del Security Group alterado.

    Raises:
        botocore.exceptions.ClientError: Si LocalStack falla al describir el
            grupo o al aplicar la regla de autorización en la API.
        IndexError: Si no se encuentra ningún Security Group con el nombre
            especificado en la respuesta de la API.
    """
    # Consulta la API de EC2 filtrando por el nombre contractual del Security Group
    sgs = ec2.describe_security_groups(GroupNames=["production-perimeter-sg"])

    # Extrae el GroupId único (ej. 'sg-xxxx') del primer grupo retornado en la lista
    sg_id = sgs["SecurityGroups"][0]["GroupId"]

    # Imprime en la salida estándar el inicio de la intervención fuera de banda
    print(f"[!] Alterando configuración en caliente en {sg_id}...")

    # Invoca la API AuthorizeSecurityGroupIngress para anexar la regla en caliente
    ec2.authorize_security_group_ingress(
        # Vincula la regla directamente al ID alfanumérico del Security Group
        GroupId=sg_id,
        # Define la lista de especificaciones de permisos de red entrantes
        IpPermissions=[
            {
                # Protocolo de transporte de Capa 4 a inspeccionar
                "IpProtocol": "tcp",
                # Límite inferior del rango de puertos de destino permitido
                "FromPort": 8080,
                # Límite superior del rango de puertos de destino permitido
                "ToPort": 8080,
                # Lista de bloques CIDR IPv4 a los que se concede acceso
                "IpRanges": [
                    {
                        # Notación CIDR que representa a la totalidad del direccionamiento IPv4
                        "CidrIp": "0.0.0.0/0",
                        # Metadato descriptivo que documenta el propósito de la regla en auditorías
                        "Description": "Hotfix temporal bypass",
                    }
                ],
            }
        ],
    )

    # Notifica al operador la consumación de la inyección de desvío
    print("[ALERTA] Drift inyectado: Puerto 8080 expuesto a todo Internet (0.0.0.0/0).")

    # Retorna el ID del grupo modificado para trazabilidad o pruebas unitarias
    return sg_id


# Bloque de ejecución principal (Entry Point)
if __name__ == "__main__":
    # Ejecuta la rutina de inyección solo si el archivo se invoca directamente por CLI
    inject_unauthorized_drift()