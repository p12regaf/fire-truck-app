import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO
import os

log = logging.getLogger(__name__)

class AlarmMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de ALARMA
    de la fuente de alimentación (ej. sobretemperatura). Cuando se detecta,
    inicia la secuencia de apagado controlado de la aplicación y el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="AlarmMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        alarm_config = config.get('system', {}).get('alarm_monitor', {})
        self.pin = alarm_config.get('pin')
        
        pull_config_str = alarm_config.get('pull_up_down', 'PUD_DOWN').upper()

        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en config de AlarmMonitor: '{pull_config_str}'.")
            self.pin = None
            return

        # La lógica de disparo ahora se basa en la configuración
        if self.pull_up_down == GPIO.PUD_UP:
            self.trigger_state = GPIO.LOW
        else: # GPIO.PUD_DOWN
            self.trigger_state = GPIO.HIGH

    def run(self):
        if not self._setup():
            log.error("AlarmMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"AlarmMonitor iniciado. Vigilando pin {self.pin} para estado '{'HIGH' if self.trigger_state == GPIO.HIGH else 'LOW'}'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡ALARMA DE FUENTE DE ALIMENTACIÓN DETECTADA! Iniciando apagado de emergencia.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                log.info("AlarmMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0)
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando apagado del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. Apagar el sistema operativo
                self._shutdown_system()
                break

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("AlarmMonitor detenido.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para AlarmMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("AlarmMonitor: limpieza finalizada.")

    def _perform_update_if_pending(self):
        """
        Comprueba si hay una actualización pendiente y ejecuta el script de instalación.
        """
        update_flag_path = "/tmp/update_pending"
        install_script_path = "/home/cosigein/fire-truck-app/scripts/install_update.sh"
        
        if os.path.exists(update_flag_path):
            log.critical("ACTUALIZACIÓN PENDIENTE DETECTADA. Ejecutando script de instalación antes de apagar.")
            try:
                # Damos un timeout generoso, pero el apagado no esperará indefinidamente
                result = subprocess.run(
                    [install_script_path],
                    capture_output=True,
                    text=True,
                    timeout=300 # 5 minutos de timeout
                )
                log.info(f"Script de instalación finalizado con código de salida: {result.returncode}")
                log.info(f"Salida del script:\n{result.stdout}")
                if result.stderr:
                    log.error(f"Errores del script de instalación:\n{result.stderr}")
            except subprocess.TimeoutExpired:
                log.critical("El script de instalación tardó demasiado (timeout). Forzando apagado.")
            except Exception as e:
                log.critical(f"Fallo al ejecutar el script de instalación: {e}")

    # Modifica el método de apagado/reinicio del sistema
    def _shutdown_system(self):
        log.info("Comprobando si hay actualizaciones pendientes antes del apagado final.")
        self._perform_update_if_pending()

        log.critical("APAGANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'shutdown', 'now'])
            
            # Línea para pruebas
            # print("SIMULACIÓN: sudo shutdown now")
            log.info("Comando de apagado del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de apagado del sistema: {e}")