import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO
import os

log = logging.getLogger(__name__)

class RebootMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de REINICIO
    de la fuente de alimentación. Cuando se detecta, inicia la secuencia de 
    apagado controlado de la aplicación y luego reinicia el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="RebootMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        reboot_config = config.get('system', {}).get('reboot_monitor', {})
        self.pin = reboot_config.get('pin')
        
        pull_config_str = reboot_config.get('pull_up_down', 'PUD_DOWN').upper()

        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en config de RebootMonitor: '{pull_config_str}'.")
            self.pin = None
            return

        # La lógica de disparo se basa en la configuración.
        if self.pull_up_down == GPIO.PUD_UP:
            # Si se usa pull-up interno, el estado normal es HIGH, se activa en LOW.
            self.trigger_state = GPIO.LOW 
        else: # GPIO.PUD_DOWN
            # Si se usa pull-down, el estado normal es LOW. Para que se active en LOW,
            # se necesita un circuito externo que mantenga el pin en HIGH.
            self.trigger_state = GPIO.LOW

    def run(self):
        if not self._setup():
            log.error("RebootMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"RebootMonitor iniciado. Vigilando pin {self.pin} para estado 'LOW'...")

        while not self.shutdown_event.is_set():
            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡Señal de REINICIO del sistema detectada! Iniciando apagado y reinicio.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                log.info("RebootMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0)
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando reinicio del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. REINICIAR el sistema operativo
                self._reboot_system()
                break

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("RebootMonitor detenido.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para RebootMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("RebootMonitor: limpieza finalizada.")

    # Modifica el método de apagado/reinicio del sistema
    def _reboot_system(self):
        log.critical("REINICIANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'reboot'])

            # Línea para pruebas
            # print("SIMULACIÓN: sudo reboot")
            log.info("Comando de reinicio del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de reinicio del sistema: {e}")