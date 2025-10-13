import argparse
import logging
import signal
import sys
import time
from queue import Queue

# prueba de actualizacion IIIIIIIIII

from src.core.app_controller import AppController
from src.utils.config_loader import ConfigLoader
from src.utils.unified_logger import setup_logging

# Para la GUI, solo se importa si es necesario para evitar dependencias
# innecesarias en el modo de servicio.
try:
    from src.gui.main_window import MainWindow
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


def main():
    """Punto de entrada principal de la aplicación fire-truck-app."""
    parser = argparse.ArgumentParser(description="Sistema de Monitorización de Vehículos (fire-truck-app)")
    parser.add_argument("--config", default="config/config.yaml", help="Ruta al archivo de configuración.")
    parser.add_argument("--gui", action="store_true", help="Lanzar la aplicación con la interfaz gráfica.")
    args = parser.parse_args()

    # 1. Cargar configuración
    try:
        config_loader = ConfigLoader(args.config)
        config = config_loader.get_config()
    except FileNotFoundError:
        print(f"Error: El archivo de configuración '{args.config}' no fue encontrado.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error al cargar la configuración: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Configurar logging centralizado
    setup_logging(config)
    log = logging.getLogger(__name__)
    log.info("Iniciando aplicación fire-truck-app...")

    # 3. Crear el controlador principal de la aplicación
    app_controller = AppController(config)

    # 4. Configurar manejo de señales para un apagado ordenado
    def signal_handler(sig, frame):
        log.warning(f"Señal {signal.Signals(sig).name} recibida. Iniciando apagado...")
        app_controller.shutdown()
        # Si se ejecuta la GUI, también se debe cerrar.
        if 'main_window' in locals() and main_window.is_running():
            main_window.close()

    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler) # systemctl stop

    # 5. Iniciar los servicios del backend
    app_controller.start()

    # 6. Decidir si arrancar en modo GUI o headless
    use_gui = args.gui or config.get('system', {}).get('start_with_gui', False)

    if use_gui:
        if not GUI_AVAILABLE:
            log.error("Se solicitó la GUI, pero los componentes (ej. tkinter) no están disponibles. Saliendo.")
            app_controller.shutdown()
            sys.exit(1)
        
        log.info("Iniciando en modo GUI...")
        main_window = MainWindow(app_controller)
        main_window.run() # Esto bloquea hasta que la ventana se cierra
        log.info("La ventana de la GUI se ha cerrado.")
        # Asegurarse de que el backend se apaga si la GUI se cierra primero
        if not app_controller.is_shutting_down():
            app_controller.shutdown()
            
    else:
        log.info("Iniciando en modo headless (servicio).")
        # Mantener el hilo principal vivo para que los hilos de trabajo puedan operar.
        # El bucle se romperá cuando el evento de apagado se active.
        while not app_controller.is_shutting_down():
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                # Esto es redundante si signal_handler funciona, pero es una buena práctica
                break

    log.info("La aplicación fire-truck-app se ha detenido limpiamente.")
    # Limpieza final de GPIO si se usó
    try:
        import RPi.GPIO as GPIO
        GPIO.cleanup()
        log.info("Limpieza de GPIO completada.")
    except (RuntimeError, ImportError):
        # No hacer nada si RPi.GPIO no está disponible o ya fue limpiado
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()