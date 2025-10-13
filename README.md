# Sistema de Monitorización de Vehículos (fire-truck-app)

Esta aplicación refactorizada proporciona una plataforma modular y robusta para la adquisición, registro y transmisión de datos de vehículos.

## Arquitectura

*   **Punto de Entrada Único:** `main.py`
*   **Orquestador Central:** `src.core.app_controller.AppController`
*   **Comunicación:** Colas de mensajes (`queue.Queue`) para IPC.
*   **Configuración:** `config/config.yaml`
*   **Módulos:** Separación clara de responsabilidades en `data_acquirers`, `transmitters`, `gui`, etc.

## Instalación

1.  Clonar el repositorio.
2.  Crear y activar un entorno virtual:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
3.  Instalar dependencias:
    ```bash
    pip install -r requirements.txt
    ```

## Uso

### Modo Servicio (Headless)
El sistema está diseñado para funcionar como un servicio `systemd`.

1.  Copie `services/app.service` a `/etc/systemd/system/`.
2.  Asegúrese de que las rutas en el archivo `.service` son correctas.
3.  Inicie y habilite el servicio:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl start app.service
    sudo systemctl enable app.service
    ```
4.  Para ver los logs:
    ```bash
    journalctl -u app.service -f
    ```

### Modo con Interfaz Gráfica (GUI)
Para desarrollo o depuración, puede lanzar la aplicación con su GUI:

```bash
python3 main.py --gui