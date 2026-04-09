import logging
import subprocess
import threading
import time
try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    GPIO = None
import os

log = logging.getLogger(__name__)

class RebootMonitor(threading.Thread):
    """
    Gestiona la señal de 'handshake' con la fuente de alimentación.
    Al arrancar, este hilo pone un pin GPIO en estado ALTO (HIGH) para notificar
    a la fuente de alimentación que la Raspberry Pi ha arrancado correctamente.
    Si esta señal no se establece, la fuente podría reiniciar el sistema.
    """

    def __init__(self, config: dict, app_controller):
        super().__init__(name="RebootMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        reboot_config = config.get('system', {}).get('reboot_monitor', {})
        self.pin = reboot_config.get('pin')
        # El valor de pull_up_down se ignora ya que el pin es de salida.
        self.pull_up_down = None

    def run(self):
        if not self._setup():
            log.error("RebootMonitor no pudo inicializarse. El hilo terminará.")
            return

        # Esperamos un momento para que el self-test se complete si aún no lo ha hecho.
        # En AppController.start() se ejecuta de forma síncrona antes de arrancar los hilos,
        # así que ya debería estar listo.
        
        if not self.app_controller.self_test_passed:
            log.critical("RebootMonitor: El Self-Test HA FALLADO. No se establecerá el pin en ALTO.")
            log.critical("Esto provocará que la fuente de alimentación reinicie el sistema para intentar una recuperación (rollback).")
            return

        # No hay bucle. La única tarea es poner el pin en ALTO.
        # La configuración del pin como salida se hace en AppController.
        if self.app_controller.simulate:
            log.info("RebootMonitor (Simulado): Saltando salida física GPIO.")
            return

        try:
            GPIO.output(self.pin, GPIO.HIGH)
            log.info(f"RebootMonitor: Pin {self.pin} establecido en ALTO (HIGH).")
            
            # Si hemos llegado aquí y el self-test pasó, marcamos como estable
            self.app_controller.update_manager.mark_as_stable()
            log.info("RebootMonitor: Sistema marcado como ESTABLE en el gestor de actualizaciones.")
            
        except Exception as e:
            log.critical(f"RebootMonitor: No se pudo establecer el pin {self.pin} en ALTO: {e}")
            
        # El trabajo de este hilo ha terminado. El pin se mantendrá en ALTO.
        log.info("RebootMonitor ha completado su tarea y el hilo finalizará.")

    def _setup(self) -> bool:
        if self.pin is None:
            log.critical("No se ha especificado un pin GPIO para RebootMonitor en la configuración.")
            return False
        return True

    def _cleanup(self):
        # La limpieza de GPIO es global, no hay nada que hacer aquí.
        log.debug("RebootMonitor: limpieza finalizada.")