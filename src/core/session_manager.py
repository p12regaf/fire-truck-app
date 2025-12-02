import json
import logging
import os
from datetime import datetime
from threading import Lock

log = logging.getLogger(__name__)

class SessionManager:
    """
    Gestiona la creación de archivos de log por sesión.
    """
    
    FOLDER_NAME_MAP = {
        "can": "CAN",
        "gps": "GPS",
        "estabilometro": "estabilidad",
        "rotativo": "ROTATIVO"
    }

    FILE_PREFIX_MAP = {
        "can": "CAN",
        "gps": "GPS",
        "estabilometro": "ESTABILIDAD",
        "rotativo": "ROTATIVO"
    }
    
    COLUMN_HEADERS = {
        "estabilometro": "ax;ay;az;gx;gy;gz;roll;pitch;yaw;timeantwifi;usciclo1;usciclo2;usciclo3;usciclo4;usciclo5;si;accmag;microsds;k3\n",
        "gps": "Timestamp;FechaGPS;HoraGPS;Latitud;Longitud;Altitud;HDOP;Fix;NumSats;Velocidad(km/h)\n",
        "rotativo": "Timestamp;Estado\n",
        "can": "Timestamp;InterfazCAN;PGN;NumBytes;Datos\n"
    }

    def __init__(self, config: dict):
        self.config = config
        paths_config = config.get('paths', {})
        system_config = config.get('system', {})

        self.data_root = paths_config.get('data_root', '/tmp/fire-truck-app_data')
        self.db_path = paths_config.get('session_db', '/tmp/fire-truck-app_session.json')
        
        device_number = system_config.get('device_number', '000')
        self.device_name = f"DOBACK{device_number}"

        self.lock = Lock()
        
        now = datetime.now()
        self.today_str_ymd = now.strftime('%Y%m%d')
        self.session_time = now

        # Inicializar sesión y guardar rutas activas para que el FTP no las toque
        self.current_session_id = self._initialize_session()
        self.active_log_files = {} # Almacena {tipo: ruta_completa}
        
        log.info(f"Sesión activa: {self.current_session_id} para el día {self.today_str_ymd}.")

    def _load_session_db(self) -> dict:
        if not os.path.exists(self.db_path):
            return {"session_counters": {}}
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"session_counters": {}}

    def _save_session_db(self, session_data: dict):
        try:
            with open(self.db_path, 'w') as f:
                json.dump(session_data, f, indent=4)
        except IOError as e:
            log.error(f"Error guardando DB de sesión: {e}")
            
    def _initialize_session(self) -> int:
        with self.lock:
            session_data = self._load_session_db()
            counters = session_data.get("session_counters", {})
            last_session_today = counters.get(self.today_str_ymd, 0)
            new_session_id = last_session_today + 1
            counters[self.today_str_ymd] = new_session_id
            session_data["session_counters"] = counters
            self._save_session_db(session_data)
            return new_session_id

    def _get_folder_name(self, internal_type: str) -> str:
        return self.FOLDER_NAME_MAP.get(internal_type, internal_type.upper())

    def _get_file_prefix_name(self, internal_type: str) -> str:
        return self.FILE_PREFIX_MAP.get(internal_type, internal_type.upper())

    def ensure_data_directories(self, active_data_types: list):
        for data_type in active_data_types:
            folder_name = self._get_folder_name(data_type)
            dir_path = os.path.join(self.data_root, folder_name)
            os.makedirs(dir_path, exist_ok=True)

    def get_log_path(self, data_type: str) -> str:
        """
        Obtiene la ruta única para esta sesión.
        Formato: TIPO_DOBACKXXX_YYYYMMDD_S{ID}.txt
        """
        if data_type in self.active_log_files:
            return self.active_log_files[data_type]

        folder_name = self._get_folder_name(data_type)
        file_prefix = self._get_file_prefix_name(data_type)
        # CAMBIO: Se añade _S{session_id} al nombre del archivo
        filename = f"{file_prefix}_{self.device_name}_{self.today_str_ymd}_S{self.current_session_id}.txt"
        full_path = os.path.join(self.data_root, folder_name, filename)
        
        # Registrar como activo
        self.active_log_files[data_type] = full_path
        return full_path

    def get_realtime_log_path(self, data_type: str) -> str:
        folder_name = self._get_folder_name(data_type)
        file_prefix = self._get_file_prefix_name(data_type)
        filename = f"{file_prefix}_{self.device_name}_RealTime.txt"
        return os.path.join(self.data_root, folder_name, filename)
    
    def is_file_active(self, filepath: str) -> bool:
        """Comprueba si un archivo pertenece a la sesión actual activa."""
        return filepath in self.active_log_files.values()

    def get_session_header(self, data_type: str) -> str:
        type_name = self._get_file_prefix_name(data_type)
        timestamp_str = self.session_time.strftime('%d/%m/%Y %H:%M:%S')
        terminator = ";\n"
        header = (
            f"\n{type_name};{timestamp_str};{self.device_name};"
            f"Sesión:{self.current_session_id}{terminator}"
        )
        return header

    def get_column_header(self, data_type: str) -> str:
        return self.COLUMN_HEADERS.get(data_type, "")