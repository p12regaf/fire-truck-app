# Contenido COMPLETO para: ./src/core/session_manager.py

import json
import logging
import os
from datetime import datetime
from threading import Lock

log = logging.getLogger(__name__)

class SessionManager:
    """
    Gestiona la creación de archivos de log, rutas y una sesión global por cada
    arranque de la aplicación.
    """
    def __init__(self, config: dict):
        self.config = config
        paths_config = config.get('paths', {})
        self.data_root = paths_config.get('data_root', '/tmp/hums_data')
        self.db_path = paths_config.get('session_db', '/tmp/hums_session.json')
        self.device_id = config.get('system', {}).get('device_number', '000')
        self.lock = Lock()
        
        now = datetime.now()
        self.today_str = now.strftime('%Y%m%d')
        self.session_time_str = now.strftime('%H-%M-%S')
        
        self.current_session_id = self._initialize_session() # <--- Esta línea necesita el método de abajo
        
        session_folder_name = f"session_{self.current_session_id:03d}_{self.session_time_str}"
        self.session_path = os.path.join(self.data_root, self.today_str, session_folder_name)
        
        try:
            os.makedirs(self.session_path, exist_ok=True)
            log.info(f"Sesión activa: {self.current_session_id}. Directorio de datos: {self.session_path}")
        except OSError as e:
            log.critical(f"No se pudo crear el directorio de la sesión: {self.session_path}. Error: {e}")
            raise

    def _load_session_db(self) -> dict:
        """Carga el estado de la sesión desde el archivo JSON."""
        if not os.path.exists(self.db_path):
            log.warning(f"Archivo de sesión no encontrado en {self.db_path}. Creando uno nuevo.")
            return {"session_counters": {}}
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.error(f"No se pudo cargar o parsear el archivo de sesión. Creando uno nuevo. Error: {e}")
            return {"session_counters": {}}

    def _save_session_db(self, session_data: dict):
        """Guarda el estado actual de la sesión en el archivo JSON."""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(session_data, f, indent=4)
        except IOError as e:
            log.error(f"No se pudo guardar el archivo de sesión en {self.db_path}: {e}")
            
    # --- MÉTODO QUE FALTABA ---
    def _initialize_session(self) -> int:
        """
        Determina el ID de la sesión actual.
        Si es un nuevo día, el contador de sesión se resetea a 1.
        Si es el mismo día, el contador se incrementa.
        Este método se ejecuta una sola vez al inicio de la aplicación.
        """
        with self.lock:
            session_data = self._load_session_db()
            counters = session_data.get("session_counters", {})
            
            last_session_today = counters.get(self.today_str, 0)
            
            new_session_id = last_session_today + 1
            
            counters[self.today_str] = new_session_id
            session_data["session_counters"] = counters
            self._save_session_db(session_data)
            
            return new_session_id
    # --- FIN DEL MÉTODO QUE FALTABA ---

    def get_log_path(self, data_type: str) -> str:
        """
        Obtiene la ruta del archivo de log para un tipo de dato dentro de la sesión actual.
        """
        filename = f"{self.today_str}_{self.device_id}_{data_type.upper()}.log"
        return os.path.join(self.session_path, filename)

    def get_realtime_log_path(self, data_type: str) -> str:
        """Obtiene la ruta para el archivo de estado en tiempo real dentro de la sesión actual."""
        filename = f"{self.today_str}_{self.device_id}_{data_type.upper()}_RealTime.txt"
        return os.path.join(self.session_path, filename)