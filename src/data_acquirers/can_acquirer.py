# Archivo: src/data_acquirers/can_acquirer.py

import logging
import subprocess
import threading
import time
from typing import Optional, Dict, Any, List
import can

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class CANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="CANAcquirer", config_key="can")
        
        self.interface = self.config.get('interface', 'can0')
        self.bitrate = self.config.get('bitrate', 250000)
        
        self.pgn_config = self.config.get('pgn_to_listen', [])
        self.pgn_map = {item['pgn']: item for item in self.pgn_config}

        self.requests_config = self.config.get('requests', {})
        self.tx_messages_config = self.config.get('tx_messages', [])
        self.net_mgmt_config = self.config.get('network_management', {})

        # --- Atributos de estado ---
        # ELIMINADO: self.candump_proc ya no es necesario.
        self.can_bus: Optional[can.interface.Bus] = None
        self.request_thread: Optional[threading.Thread] = None
        self.tx_threads: List[threading.Thread] = []

    def _initialize_can_interface(self) -> bool:
        """Configura la interfaz CAN usando el comando 'ip link'."""
        log.info(f"Configurando interfaz CAN '{self.interface}' a {self.bitrate} bps...")
        try:
            subprocess.run(["sudo", "ip", "link", "set", self.interface, "down"], check=False, capture_output=True)
            subprocess.run(["sudo", "ip", "link", "set", self.interface, "type", "can", "bitrate", str(self.bitrate)], check=True, capture_output=True)
            subprocess.run(["sudo", "ip", "link", "set", self.interface, "up"], check=True, capture_output=True)
            log.info(f"Interfaz '{self.interface}' activa a {self.bitrate} bps.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            log.critical(f"Fallo al inicializar la interfaz CAN '{self.interface}'. Error: {getattr(e, 'stderr', e)}. ¿can-utils y sudo están disponibles?")
            return False
            
    # --- Lógica de Transmisión (sin cambios) ---
    # ... (Los métodos _send_address_claim, _request_loop, _tx_loop no necesitan cambios)
    def _send_address_claim(self):
        """Envía un mensaje J1939 Address Claimed si está configurado."""
        claim_config = self.net_mgmt_config.get('address_claiming', {})
        if not claim_config.get('enable') or not self.can_bus:
            return

        try:
            name = int(claim_config.get('name', 0))
            name_bytes = list(name.to_bytes(8, 'little'))
            sa = int(claim_config.get('source_address', 254))
            arbitration_id = 0x18EEFF00 | sa
            
            msg = can.Message(arbitration_id=arbitration_id, data=name_bytes, is_extended_id=True)
            self.can_bus.send(msg)
            log.info(f"Enviado Address Claim (NAME: {name:#018x}, SA: {sa:#04x})")
        except Exception as e:
            log.error(f"Error al enviar Address Claim: {e}")

    def _request_loop(self):
        """Hilo para enviar peticiones de PGN periódicamente."""
        pgns_to_request = self.requests_config.get('pgns', [])
        interval_sec = self.requests_config.get('interval_ms', 5000) / 1000.0
        
        if not pgns_to_request or not self.can_bus:
            log.warning("El hilo de peticiones de PGN termina (no hay PGNs o bus no disponible).")
            return
            
        log.info(f"Iniciado hilo de peticiones de PGN cada {interval_sec}s para PGNs: {pgns_to_request}")
        while not self.shutdown_event.is_set():
            for pgn in pgns_to_request:
                try:
                    data = list(pgn.to_bytes(3, 'little'))
                    msg = can.Message(arbitration_id=0x18EAFF00, data=data, is_extended_id=True)
                    self.can_bus.send(msg)
                    log.debug(f"Petición para PGN {pgn} ({pgn:#06x}) enviada.")
                except Exception as e:
                    log.error(f"Error enviando petición para PGN {pgn}: {e}")
            
            self.shutdown_event.wait(interval_sec)

    def _tx_loop(self, tx_config: Dict[str, Any]):
        """Hilo para enviar un mensaje CAN periódico específico."""
        pgn = tx_config.get('pgn')
        rate_sec = tx_config.get('rate_ms', 1000) / 1000.0
        data_str = tx_config.get('data', '')
        prio = tx_config.get('priority', 6)
        sa = self.net_mgmt_config.get('address_claiming', {}).get('source_address', 0x80)

        if not all([pgn, data_str]) or not self.can_bus:
            log.warning(f"El hilo de TX para PGN {pgn} termina (configuración incompleta o bus no disponible).")
            return

        try:
            data_bytes = bytes.fromhex(data_str.replace(" ", ""))
            arbitration_id = (prio << 26) | (pgn << 8) | sa
        except Exception as e:
            log.error(f"Error al configurar el hilo TX para PGN {pgn}: {e}")
            return
            
        log.info(f"Iniciado hilo de TX para PGN {pgn} cada {rate_sec}s.")
        while not self.shutdown_event.is_set():
            try:
                msg = can.Message(arbitration_id=arbitration_id, data=data_bytes, is_extended_id=True)
                self.can_bus.send(msg)
                log.debug(f"Mensaje de TX para PGN {pgn} enviado.")
            except Exception as e:
                log.error(f"Error en el hilo de TX para PGN {pgn}: {e}")
            
            self.shutdown_event.wait(rate_sec)

    # --- CAMBIO CLAVE: Métodos del Ciclo de Vida de BaseAcquirer ---

    def _setup(self) -> bool:
        if not self._initialize_can_interface():
            return False

        # --- CAMBIO: Construir filtros para python-can, no para candump ---
        can_filters = []
        if self.pgn_config:
            for pgn_item in self.pgn_config:
                pgn = pgn_item['pgn']
                # El filtro se aplica sobre el ID completo de 29 bits.
                # La máscara 0x1FFFF00 aísla el PGN.
                can_id = pgn << 8
                can_mask = 0x1FFFF00 # Máscara para PGN
                can_filters.append({"can_id": can_id, "can_mask": can_mask, "extended": True})
            log.info(f"Filtros CAN para el kernel configurados para {len(can_filters)} PGNs.")
        else:
            log.warning("CANAcquirer: No se especificaron PGNs. Escuchando todo el tráfico.")

        # --- CAMBIO: Inicializar el bus de python-can con los filtros para recepción y envío ---
        try:
            self.can_bus = can.interface.Bus(
                channel=self.interface, 
                interface='socketcan',
                can_filters=can_filters
            )
            log.info("Bus de python-can inicializado para recepción y envío.")
        except Exception as e:
            log.critical(f"No se pudo abrir el bus de python-can: {e}. El módulo no funcionará.")
            return False

        # ELIMINADO: Ya no se inicia candump.
        # self.candump_proc = ...

        # Realizar acciones de red (esto no cambia)
        self._send_address_claim()

        # Iniciar hilos para tareas periódicas (esto no cambia)
        if self.requests_config.get('enable'):
            self.request_thread = threading.Thread(target=self._request_loop, name="CANRequestThread", daemon=True)
            self.request_thread.start()

        for i, tx_conf in enumerate(self.tx_messages_config):
            if tx_conf.get('enable'):
                thread = threading.Thread(target=self._tx_loop, args=(tx_conf,), name=f"CANTxThread-{i}", daemon=True)
                self.tx_threads.append(thread)
                thread.start()
        
        return True

    def _acquire_data(self):
        # --- CAMBIO RADICAL: Bucle de adquisición mucho más simple y robusto ---
        if not self.can_bus:
            self.shutdown_event.wait(1.0) # Esperar si el bus no se inició
            return

        # Esperar un mensaje con un timeout. Esto evita consumir 100% de CPU.
        msg = self.can_bus.recv(timeout=1.0)
        
        if msg is None:
            # Timeout, no hay mensaje. El bucle principal continuará.
            return
            
        # Si llegamos aquí, hemos recibido un objeto can.Message.
        # No necesitamos parsear texto.
        
        # Extraer PGN y procesar
        pgn = (msg.arbitration_id >> 8) & 0x1FFFF
        if pgn in self.pgn_map:
            parsed_data = self._parse_j1939_message(pgn, msg.data)
            
            if parsed_data:
                pgn_info = self.pgn_map[pgn]
                
                final_data = {
                    "pgn_name": pgn_info['name'],
                    "pgn": pgn,
                    "value": parsed_data['value'],
                    "unit": pgn_info.get('unit', 'N/A'),
                    "raw_data": msg.data.hex().upper(),
                    "interface": self.interface,
                    "arbitration_id_hex": f"{msg.arbitration_id:08X}"
                }
                packet = self._create_data_packet("can", final_data)
                self.data_queue.put(packet)
                # Este log ahora debería aparecer
                log.debug(f"Mensaje CAN procesado para {pgn_info['name']}: {final_data['value']} {final_data['unit']}")

    def _cleanup(self):
        log.info("Limpiando CANAcquirer...")
        
        # ELIMINADO: Ya no hay proceso candump que terminar.
        
        if self.can_bus:
            self.can_bus.shutdown()
            log.info("Bus de python-can cerrado.")

    # --- Métodos de Parseo ---
    # ELIMINADO: _parse_candump_line ya no es necesario
    # ELIMINADO: _build_candump_filter ya no es necesario

    def _parse_j1939_message(self, pgn: int, data: bytes) -> Optional[Dict[str, Any]]:
        """
        Parsea los datos de un mensaje J1939 basado en su PGN.
        Esta función no cambia.
        """
        value = None
        try:
            if pgn == 61444: # PGN: 0xF004 - EEC1 - Engine Speed
                raw_val = int.from_bytes(data[3:5], 'little')
                value = raw_val * 0.125
            elif pgn == 65265: # PGN: 0xFEF1 - LFE1 - Wheel-Based Vehicle Speed
                raw_val = int.from_bytes(data[1:3], 'little')
                value = raw_val / 256.0
            
            if value is not None:
                return {"value": round(value, 2)}
                
        except IndexError:
            log.warning(f"Índice fuera de rango al parsear PGN {pgn}. Longitud de datos: {len(data)}")
        return None