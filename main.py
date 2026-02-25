# -*- coding: utf-8 -*-

"""
=============================================================================
 ENTRY POINT — fire-truck-app
=============================================================================
 Arranque principal de la aplicación en modo servicio (headless) o GUI.
 Preparado para systemd, con apagado limpio y dependencias explícitas.
=============================================================================
"""

import argparse
import logging
import signal
import sys
import time
from typing import Optional

from src.core.app_controller import AppController
from src.utils.config_loader import ConfigLoader
from src.utils.unified_logger import setup_logging

# Importación diferida de la GUI
try:
    from src.gui.main_window import MainWindow
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


def main() -> None:
    """Punto de entrada principal de fire-truck-app."""

    # ---------------------------------------------------------------------
    # Argumentos CLI
    # ---------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Sistema de Monitorización de Vehículos (fire-truck-app)"
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Ruta al archivo de configuración."
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Lanzar la aplicación con interfaz gráfica."
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Activar el hilo de monitoreo de pruebas (System Monitor)."
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Activar modo simulación (datos ficticios)."
    )
    args = parser.parse_args()

    # ---------------------------------------------------------------------
    # Carga de configuración
    # ---------------------------------------------------------------------
    try:
        config_loader = ConfigLoader(args.config)
        config = config_loader.get_config()
    except FileNotFoundError:
        print(
            f"Error: El archivo de configuración '{args.config}' no fue encontrado.",
            file=sys.stderr
        )
        sys.exit(1)
    except Exception as exc:
        print(f"Error al cargar la configuración: {exc}", file=sys.stderr)
        sys.exit(1)

    # ---------------------------------------------------------------------
    # Logging centralizado
    # ---------------------------------------------------------------------
    setup_logging(config)
    log = logging.getLogger(__name__)
    log.info("Iniciando fire-truck-app")

    # ---------------------------------------------------------------------
    # Controlador principal
    # ---------------------------------------------------------------------
    app_controller = AppController(config, simulate=args.simulate)

    # ---------------------------------------------------------------------
    # Sistema de Monitoreo (Opcional)
    # ---------------------------------------------------------------------
    monitor = None
    if args.test_mode or config.get("system", {}).get("test_mode", False):
        try:
            from src.tests.system_monitor import SystemMonitor
            monitor = SystemMonitor(app_controller)
            app_controller.register_monitor(monitor.get_queue())
            monitor.start()
            log.info("System Monitor integrado y funcionando.")
        except Exception as exc:
            log.error(f"No se pudo iniciar el System Monitor: {exc}")

    # Referencia explícita a la GUI (evita hacks con locals)
    main_window: Optional[MainWindow] = None

    # ---------------------------------------------------------------------
    # Manejo de señales
    # ---------------------------------------------------------------------
    def signal_handler(sig, _frame) -> None:
        sig_name = signal.Signals(sig).name
        log.warning("Señal %s recibida. Iniciando apagado ordenado.", sig_name)

        if not app_controller.is_shutting_down():
            app_controller.shutdown()

        if main_window is not None and main_window.is_running():
            main_window.close()

    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # systemctl stop

    # ---------------------------------------------------------------------
    # Arranque backend
    # ---------------------------------------------------------------------
    try:
        app_controller.start()
    except Exception as exc:
        log.exception("Error crítico durante el arranque del backend: %s", exc)
        app_controller.shutdown()
        sys.exit(1)

    # ---------------------------------------------------------------------
    # Decidir modo de ejecución
    # ---------------------------------------------------------------------
    use_gui = bool(
        args.gui or
        config.get("system", {}).get("start_with_gui", False)
    )

    # ---------------------------------------------------------------------
    # Modo GUI
    # ---------------------------------------------------------------------
    if use_gui:
        if not GUI_AVAILABLE:
            log.error(
                "Se solicitó la GUI pero no están disponibles las dependencias gráficas."
            )
            app_controller.shutdown()
            sys.exit(1)

        log.info("Arrancando en modo GUI")
        main_window = MainWindow(app_controller)

        try:
            main_window.run()  # Bloquea hasta cierre
        except Exception as exc:
            log.exception("Error en la GUI: %s", exc)
        finally:
            if not app_controller.is_shutting_down():
                app_controller.shutdown()

    # ---------------------------------------------------------------------
    # Modo headless (servicio systemd)
    # ---------------------------------------------------------------------
    else:
        log.info("Arrancando en modo headless (servicio)")
        try:
            while not app_controller.is_shutting_down():
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt recibido")
        finally:
            if not app_controller.is_shutting_down():
                app_controller.shutdown()

    # ---------------------------------------------------------------------
    # Limpieza final
    # ---------------------------------------------------------------------
    if monitor is not None:
        monitor.stop()
        monitor.join(timeout=2.0)

    log.info("fire-truck-app detenido limpiamente")

    # Limpieza defensiva de GPIO (si aplica)
    if not args.simulate:
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
            log.info("GPIO cleanup completado")
        except (ImportError, RuntimeError):
            pass
    else:
        log.info("Modo SIMULACIÓN: Saltando GPIO cleanup físico.")

    sys.exit(0)


if __name__ == "__main__":
    main()