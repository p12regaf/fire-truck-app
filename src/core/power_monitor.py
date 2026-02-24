import logging
import subprocess
import threading
import time
import RPi.GPIO as GPIO 
import os
import json

log = logging.getLogger(__name__)

class PowerMonitor(threading.Thread):
    """
    Un hilo que monitoriza un pin GPIO para detectar una señal de apagado del vehículo.
    Cuando se detecta, inicia la secuencia de apagado controlado de la aplicación
    y, finalmente, apaga el sistema operativo.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="PowerMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        power_config = config.get('system', {}).get('power_monitor', {})
        self.pin = power_config.get('pin')
        
        self.shutdown_state_file = config.get('paths', {}).get('shutdown_state_file')

        pull_config_str = power_config.get('pull_up_down', 'PUD_UP').upper()
        
        try:
            self.pull_up_down = getattr(GPIO, pull_config_str)
        except AttributeError:
            log.critical(f"Valor de 'pull_up_down' inválido en la configuración: '{pull_config_str}'.")
            log.critical("Debe ser 'PUD_UP' o 'PUD_DOWN'.")
            self.pin = None 
            return

        # La lógica de disparo ahora se basa en la configuración
        if self.pull_up_down == GPIO.PUD_UP:
            self.trigger_state = GPIO.LOW  # Con pull-up, el pin está en HIGH y se dispara en LOW.
        else: # GPIO.PUD_DOWN
            self.trigger_state = GPIO.HIGH

    def run(self):
        if not self._setup():
            log.error("PowerMonitor no pudo inicializarse. El hilo terminará.")
            return

        log.info(f"PowerMonitor iniciado. Vigilando pin {self.pin} para estado '{'LOW' if self.trigger_state == GPIO.LOW else 'HIGH'}'...")

        while not self.shutdown_event.is_set():
            if self.app_controller.simulate:
                # En modo simulación, no leemos de pins reales
                self.shutdown_event.wait(5.0)
                continue

            if GPIO.input(self.pin) == self.trigger_state:
                log.critical("¡Señal de apagado del vehículo detectada! Iniciando apagado controlado.")
                
                # 1. Indicar a la aplicación que se apague (esto ejecutará la subida final)
                self.app_controller.shutdown()
                
                # 2. Esperar a que la aplicación termine su secuencia de apagado.
                #    Esperamos por el hilo del procesador de datos, que es uno de los últimos en parar.
                log.info("PowerMonitor esperando a que los servicios de la app finalicen...")
                self.app_controller.processor_thread.join(timeout=60.0) # Espera hasta 60s
                
                if self.app_controller.processor_thread.is_alive():
                    log.error("El procesador de la app no terminó a tiempo. Forzando apagado del sistema.")
                else:
                    log.info("Los servicios de la app han finalizado correctamente.")

                # 3. Apagar el sistema operativo
                self._write_shutdown_reason("POWER_OFF")
                self._shutdown_system()
                break # Salir del bucle

            self.shutdown_event.wait(1.0) # Comprobar el estado cada segundo

        self._cleanup()
        log.info("PowerMonitor detenido.")

    def _write_shutdown_reason(self, reason: str):
        if not self.shutdown_state_file:
            log.error("No se ha configurado 'shutdown_state_file'. No se puede guardar el motivo del apagado.")
            return
        try:
            with open(self.shutdown_state_file, 'w') as f:
                json.dump({"reason": reason, "timestamp": datetime.now().isoformat()}, f)
            log.info(f"Motivo del apagado ('{reason}') guardado en {self.shutdown_state_file}")
        except IOError as e:
            log.error(f"No se pudo escribir el archivo de estado de apagado: {e}")

    def _setup(self) -> bool:
        # --- BLOQUE ELIMINADO ---
        # Ya no se necesita la comprobación de GPIO_AVAILABLE
        
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para PowerMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        log.debug("PowerMonitor: limpieza finalizada.")

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