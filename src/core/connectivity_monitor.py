import logging
import threading
import time
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

class ConnectivityMonitor(threading.Thread):
    """
    Worker that checks for internet connectivity every second and logs the status.
    """
    def __init__(self, config: dict, app_controller):
        super().__init__(name="ConnectivityMonitor")
        self.app_controller = app_controller
        self.shutdown_event = app_controller.shutdown_event
        
        # Pull log path from config if available, fallback to default
        self.log_dir = Path(config.get('paths', {}).get('app_logs', '/home/cosigein/logs'))
        self.connectivity_log = self.log_dir / "connectivity.log"
        self.host_to_ping = "8.8.8.8"
        self.check_interval = 1.0 # seconds
        self.connectivity_seen = False

    def run(self):
        log.info(f"ConnectivityMonitor iniciado. Logeando en {self.connectivity_log}")
        
        # Ensure log directory exists
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error(f"ConnectivityMonitor: No se pudo crear el directorio de logs: {e}")
            return

        while not self.shutdown_event.is_set():
            status = self._check_connectivity()
            if status:
                self.connectivity_seen = True
            self._log_status(status)
            self.shutdown_event.wait(self.check_interval)

        log.info("ConnectivityMonitor detenido.")

    def _check_connectivity(self) -> bool:
        """Checks connectivity by pinging a host."""
        try:
            # -c 1: one packet, -W 1: timeout 1s
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "1", self.host_to_ping],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return result.returncode == 0
        except Exception:
            return False

    def _log_status(self, is_connected: bool):
        """Logs connectivity status to file."""
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        status_str = "CONNECTED" if is_connected else "DISCONNECTED"
        try:
            with open(self.connectivity_log, 'a') as f:
                f.write(f"{ts} - {status_str}\n")
        except Exception as e:
            log.error(f"ConnectivityMonitor: Error al escribir en log: {e}")
