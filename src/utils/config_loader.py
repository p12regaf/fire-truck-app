import yaml
import logging

log = logging.getLogger(__name__)

class ConfigLoader:
    """Carga y proporciona acceso a la configuración desde un archivo YAML."""
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        log.info(f"Cargando configuración desde: {self.config_path}")
        with open(self.config_path, 'r') as f:
            try:
                config_data = yaml.safe_load(f)
                return config_data
            except yaml.YAMLError as e:
                log.error(f"Error al parsear el archivo YAML: {e}")
                raise
    
    def get_config(self) -> dict:
        """Devuelve el diccionario de configuración completo."""
        return self.config
    
    def get_section(self, section_name: str) -> dict:
        """Devuelve una sección específica de la configuración."""
        return self.config.get(section_name, {})