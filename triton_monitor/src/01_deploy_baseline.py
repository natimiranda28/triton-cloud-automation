"""Módulo de Despliegue de Línea Base (Golden State) en AWS LocalStack.

Este script automatiza el aprovisionamiento inicial de un Security Group
gestionado bajo principios de Infraestructura como Código (IaC). Establece una
política de privilegios mínimos en el plano de control (Control Plane),
restringiendo el tráfico de entrada únicamente al puerto HTTPS (443/TCP)
y sirviendo como punto de referencia contractual frente al Configuration Drift.
"""

# Importa el SDK oficial de AWS para interactuar con sus APIs desde Python
import boto3

# Instancia un cliente de bajo nivel para los servicios de Amazon EC2
ec2 = boto3.client(
    # Especifica el servicio de AWS sobre el que se realizarán las llamadas
    "ec2",
    # Redirige las peticiones REST al contenedor de LocalStack en lugar de AWS real
    endpoint_url="http://localhost:4566",
    # Define la región geográfica de AWS requerida por la firma criptográfica SigV4
    region_name="us-east-1",
    # Credencial de acceso ficticia aceptada por el entorno emulado de LocalStack
    aws_access_key_id="test",
    # Clave secreta ficticia requerida por el SDK para completar el handshake SigV4
    aws_secret_access_key="test",
)


def deploy_golden_state() -> str:
    """Crea un Security Group y aplica las reglas base autorizadas.

    Envía las solicitudes a la API de EC2 en LocalStack para registrar el
    Security Group corporativo y asociarle una regla de entrada (Ingress)
    exclusiva para el puerto 443, bloqueando implícitamente cualquier otro
    puerto no declarado.

    Returns:
        str: El identificador único asignado al Security Group (ej. 'sg-12345678').

    Raises:
        botocore.exceptions.ClientError: Si ocurre un fallo en la llamada API
            hacia LocalStack (ej. si el Security Group ya existe).
    """
    # Muestra en consola el inicio del aprovisionamiento
    print("[INFO] Creando Security Group corporativo en LocalStack...")

    # Ejecuta la llamada API CreateSecurityGroup para reservar el contenedor lógico
    sg = ec2.create_security_group(
        # Asigna el nombre identificador del grupo dentro de la VPC
        GroupName="production-perimeter-sg",
        # Documenta el propósito del recurso para trazabilidad y auditorías
        Description="Security Group gestionado estrictamente por IaC",
    )

    # Extrae el identificador alfanumérico generado por AWS (campo 'GroupId')
    sg_id = sg["GroupId"]

    # Invoca la acción AuthorizeSecurityGroupIngress para definir la regla de filtrado
    ec2.authorize_security_group_ingress(
        # Vincula la regla directamente al ID del Security Group creado previamente
        GroupId=sg_id,
        # Define la lista de estructuras de permisos que componen la tabla de entrada
        IpPermissions=[
            {
                # Fija el protocolo de la Capa de Transporte (Capa 4 del modelo OSI)
                "IpProtocol": "tcp",
                # Límite inferior del rango de puertos de destino permitido
                "FromPort": 443,
                # Límite superior del rango de puertos de destino permitido
                "ToPort": 443,
                # Lista de rangos IPv4 autorizados a enviar tráfico hacia este puerto
                "IpRanges": [
                    {
                        # Notación CIDR que representa a todo el direccionamiento de Internet
                        "CidrIp": "0.0.0.0/0",
                        # Comentario metadato para justificar la apertura ante auditorías
                        "Description": "HTTPS Publico",
                    }
                ],
            }
        ],
    )

    # Informa al operador la creación exitosa junto al ID del recurso
    print(f"[SUCCESS] Security Group desplegado exitosamente: {sg_id}")

    # Notifica el cumplimiento estricto de la política de privilegios mínimos
    print("[SUCCESS] Baseline aplicado: Solo puerto 443/TCP permitido.")

    # Retorna el ID asignado para permitir su consumo por tests unitarios o módulos externos
    return sg_id


# Bloque de ejecución principal (Entry Point)
if __name__ == "__main__":
    # Ejecuta la rutina de despliegue cuando el archivo se invoca directamente por CLI
    deploy_golden_state()