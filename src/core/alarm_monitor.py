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

    # Modifica el método de apagado/reinicio del sistema
    def _shutdown_system(self):
        log.critical("APAGANDO EL SISTEMA OPERATIVO AHORA.")
        try:
            # Descomenta para producción
            subprocess.call(['sudo', 'shutdown', 'now'])
            
            # Línea para pruebas
            # print("SIMULACIÓN: sudo shutdown now")
            log.info("Comando de apagado del sistema ejecutado.")
        except Exception as e:
            log.critical(f"Fallo al ejecutar el comando de apagado del sistema: {e}")