import logging
import threading
import time
from queue import Queue, Empty

log = logging.getLogger(__name__)

class SystemMonitor(threading.Thread):
    """
    A separate thread that listens to application data and performs
    integrity checks and performance monitoring.
    """
    def __init__(self, app_controller):
        super().__init__(name="SystemMonitor")
        self.app_controller = app_controller
        self.monitor_queue = Queue()
        self.shutdown_event = threading.Event()
        
        # Internal state for monitoring
        self.packet_counts = {}
        self.last_packet_time = {}
        
    def get_queue(self):
        return self.monitor_queue

    def run(self):
        log.info("System Monitor thread started.")
        
        while not self.shutdown_event.is_set():
            try:
                # Wait for data from AppController
                packet = self.monitor_queue.get(timeout=1.0)
                self._analyze_packet(packet)
            except Empty:
                # Perform periodic health checks when idle
                self._check_system_health()
                continue
            except Exception as e:
                log.error(f"Error in System Monitor loop: {e}")

        log.info("System Monitor thread stopped.")

    def stop(self):
        self.shutdown_event.set()

    def _analyze_packet(self, packet):
        """
        Performs assertions and checks on individual data packets.
        """
        data_type = packet.get('type')
        data = packet.get('data')
        
        # Track counts
        self.packet_counts[data_type] = self.packet_counts.get(data_type, 0) + 1
        self.last_packet_time[data_type] = time.time()
        
        # Perform specific checks
        if data_type == 'gps':
            self._check_gps(data)
        elif data_type == 'can':
            self._check_can(data)
        elif data_type == 'estabilometro':
            self._check_imu(data)

    def _check_gps(self, data):
        """Monitor GPS data quality and ranges."""
        if data.get('status') == 'No Fix':
            return
            
        try:
            lat = float(data.get('latitude', 0))
            lon = float(data.get('longitude', 0))
            
            # Example assertion: Coordinates should be reasonable (e.g., Earth-bound)
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                log.error(f"[MONITOR] GPS coordinates out of range: {lat}, {lon}")
        except (ValueError, TypeError):
            log.error("[MONITOR] GPS data contains non-numeric coordinates.")

    def _check_can(self, data):
        """Monitor CAN bus activity."""
        # Check if we are receiving unexpected arbitrations or values
        pass

    def _check_imu(self, data):
        """Monitor IMU data."""
        # Check for extreme gravity or rotation values
        pass

    def _check_system_health(self):
        """
        Periodic check of the overall system state.
        Runs when the queue is empty for a while.
        """
        status = self.app_controller.get_service_status()
        dead_services = [name for name, s in status.items() if s != "Running"]
        
        if dead_services:
            log.warning(f"[MONITOR] Detected inactive services: {dead_services}")
        
        # Check for data timeouts
        now = time.time()
        for data_type, last_time in self.last_packet_time.items():
            if now - last_time > 30: # 30 seconds timeout
                log.warning(f"[MONITOR] Data timeout for '{data_type}': No data for 30s.")
