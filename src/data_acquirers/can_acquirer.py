import logging
import subprocess
import select
import threading
import time
from typing import Optional, Dict, Any, List
import can

from .base_acquirer import BaseAcquirer

log = logging.getLogger(__name__)

class CANAcquirer(BaseAcquirer):
    def __init__(self, config, data_queue, shutdown_event):
        super().__init__(config, data_queue, shutdown_event, name="CANAcquirer", config_key="can")
        
        # --- Configuración del Bus CAN ---
        self.interface = self.config.get('interface', 'can0')
        self.bitrate = self.config.get('bitrate', 250000)
        
        # --- Configuración de Escucha y Parseo (del script original) ---
        self.pgn_config = self.config.get('pgn_to_listen', [])
        self.pgn_map = {item['pgn']: item for item in self.pgn_config}

        # --- Configuración de Funciones Avanzadas (del segundo script) ---
        self.requests_config = self.config.get('requests', {})
        self.tx_messages_config = self.config.get('tx_messages', [])
        self.net_mgmt_config = self.config.get('network_management', {})

        # --- Atributos de estado ---
        self.candump_proc: Optional[subprocess.Popen] = None
        self.can_bus: Optional[can.interface.Bus] = None
        self.request_thread: Optional[threading.Thread] = None
        self.tx_threads: List[threading.Thread] = []

    def _initialize_can_interface(self) -> bool:
        """Configura la interfaz CAN usando el comando 'ip link'."""
        log.info(f"Configurando interfaz CAN '{self.interface}' a {self.bitrate} bps...")
        try:
            # Es importante bajar la interfaz antes de cambiar el tipo o el bitrate
            subprocess.run(["sudo", "ip", "link", "set", self.interface, "down"], check=False, capture_output=True)
            subprocess.run(["sudo", "ip", "link", "set", self.interface, "type", "can", "bitrate", str(self.bitrate)], check=True, capture_output=True)
            subprocess.run(["sudo", "ip", "link", "set", self.interface, "up"], check=True, capture_output=True)
            log.info(f"Interfaz '{self.interface}' activa a {self.bitrate} bps.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            log.critical(f"Fallo al inicializar la interfaz CAN '{self.interface}'. Error: {getattr(e, 'stderr', e)}. ¿can-utils y sudo están disponibles?")
            return False

    def _build_candump_filter(self) -> Optional[str]:
        """Construye una cadena de filtro para candump a partir de los PGNs a escuchar."""
        if not self.pgn_config:
            log.warning("CANAcquirer: No se especificaron PGNs en 'pgn_to_listen'. Escuchando todo el tráfico.")
            return None
        
        filters = []
        for pgn_item in self.pgn_config:
            pgn = pgn_item['pgn']
            # El ID a filtrar es el PGN en su posición en el ID de 29 bits.
            # La máscara 0x1FFFF00 aísla la parte del PGN, ignorando la prioridad y la dirección de origen.
            can_id = pgn << 8
            mask = 0x1FFFF00
            filters.append(f"{can_id:08X}:{mask:08X}")
        
        return ",".join(filters) if filters else None

    # --- Lógica de Transmisión y Gestión de Red ---

    def _send_address_claim(self):
        """Envía un mensaje J1939 Address Claimed si está configurado."""
        claim_config = self.net_mgmt_config.get('address_claiming', {})
        if not claim_config.get('enable') or not self.can_bus:
            return

        try:
            # El NAME J1939 es un entero de 64 bits
            name = int(claim_config.get('name', 0))
            name_bytes = list(name.to_bytes(8, 'little')) # J1939 NAME is little-endian
            
            # PGN 60928 (0xEE00) - Address Claimed
            # El ID se construye con PGN=0xEE00, DA=0xFF (Global), SA=configurado
            sa = int(claim_config.get('source_address', 254)) # Default SA 254 (null)
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
                    # PGN 59904 (0xEA00) - Request
                    # El payload son los 3 bytes del PGN solicitado (little-endian)
                    data = list(pgn.to_bytes(3, 'little'))
                    # ID para una petición global (a la dirección 255)
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
            # Construir ID J1939: Prioridad(3) + PGN(18) + SA(8)
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

    # --- Métodos del Ciclo de Vida de BaseAcquirer ---

    def _setup(self) -> bool:
        if not self._initialize_can_interface():
            return False

        # Inicializar el bus de python-can para todas las operaciones de envío
        try:
            self.can_bus = can.interface.Bus(channel=self.interface, interface='socketcan')
            log.info("Bus de python-can inicializado para operaciones de envío.")
        except Exception as e:
            log.warning(f"No se pudo abrir el bus de python-can para enviar mensajes: {e}. El módulo funcionará en modo solo escucha.")
            self.can_bus = None

        # Iniciar candump para la escucha
        filter_str = self._build_candump_filter()
        cmd = ["candump", self.interface]
        if filter_str:
            cmd.append(filter_str)
        
        log.info(f"Iniciando candump con comando: {' '.join(cmd)}")
        try:
            self.candump_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            log.critical("Comando 'candump' no encontrado. Asegúrate de que 'can-utils' está instalado y en el PATH.")
            return False

        # Realizar acciones de red de un solo disparo
        self._send_address_claim()

        # Iniciar hilos para tareas periódicas
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
        if not self.candump_proc or not self.candump_proc.stdout:
            self.shutdown_event.wait(1.0) # Esperar si el proceso no se inició correctamente
            return

        # Esperar datos de candump sin bloquear indefinidamente
        ready, _, _ = select.select([self.candump_proc.stdout], [], [], 1.0)
        
        if not ready:
            # Comprobar si el proceso ha muerto
            if self.candump_proc.poll() is not None:
                log.error("El proceso candump ha terminado inesperadamente. Intentando reiniciar en el siguiente ciclo.")
                # Forzar un reinicio en el bucle principal de BaseAcquirer
                self.shutdown_event.set() 
            return

        line = self.candump_proc.stdout.readline()
        if not line: # Si la línea está vacía, el proceso puede haber terminado
            return

        msg = self._parse_candump_line(line)
        if not msg:
            return

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
                    "raw_data": msg.data.hex().upper()
                }
                packet = self._create_data_packet("can", final_data)
                self.data_queue.put(packet)
                log.debug(f"Mensaje de candump procesado para {pgn_info['name']}: {final_data['value']} {final_data['unit']}")

    def _cleanup(self):
        log.info("Limpiando CANAcquirer...")
        # La señal de shutdown_event ya ha sido enviada, los hilos deberían estar terminando.
        # No es estrictamente necesario hacer join en hilos daemon.
        
        if self.candump_proc:
            self.candump_proc.terminate()
            try:
                self.candump_proc.wait(timeout=2.0)
                log.info("Proceso candump terminado correctamente.")
            except subprocess.TimeoutExpired:
                self.candump_proc.kill()
                log.warning("Proceso candump no terminaba, se forzó el cierre.")
        
        if self.can_bus:
            self.can_bus.shutdown()
            log.info("Bus de python-can cerrado.")

    # --- Métodos de Parseo (del script original) ---

    def _parse_candump_line(self, line: str) -> Optional[can.Message]:
        """Parsea una línea de texto de candump a un objeto can.Message."""
        try:
            # Formato esperado: (16643243.34234) can0 18F00480#...
            # O simplemente: can0 18F00480   [8] ...
            parts = line.strip().split()
            
            # Buscar el ID del CAN, que es el primer elemento que parece un ID
            arb_id_str = ""
            data_start_index = -1
            for i, part in enumerate(parts):
                if len(part) > 3 and '[' not in part and ']' not in part:
                    try:
                        int(part, 16)
                        arb_id_str = part.split('#')[0] # Manejar formato con CAN FD
                        data_start_index = i + 2 # Saltar el ID y el '[8]'
                        break
                    except ValueError:
                        continue
            
            if not arb_id_str or data_start_index == -1: return None

            data_bytes = bytes.fromhex("".join(parts[data_start_index:]))
            
            return can.Message(
                arbitration_id=int(arb_id_str, 16),
                data=data_bytes,
                is_extended_id=True # J1939 siempre usa IDs extendidos
            )
        except (ValueError, IndexError):
            log.warning(f"No se pudo parsear la línea de candump: '{line.strip()}'")
            return None
            
    def _parse_j1939_message(self, pgn: int, data: bytes) -> Optional[Dict[str, Any]]:
        """
        Parsea los datos de un mensaje J1939 basado en su PGN.
        Esta función debería ampliarse para soportar más PGNs.
        """
        value = None
        try:
            if pgn == 61444: # PGN: 0xF004 - EEC1 - Engine Speed
                # Bytes 4-5, resolución 0.125 rpm/bit, offset 0
                raw_val = int.from_bytes(data[3:5], 'little')
                value = raw_val * 0.125
            elif pgn == 65265: # PGN: 0xFEF1 - LFE1 - Wheel-Based Vehicle Speed
                # Bytes 2-3, resolución 1/256 km/h por bit, offset 0
                raw_val = int.from_bytes(data[1:3], 'little')
                value = raw_val / 256.0
            
            # Añadir aquí más PGNs según sea necesario...

            if value is not None:
                return {"value": round(value, 2)}
                
        except IndexError:
            log.warning(f"Índice fuera de rango al parsear PGN {pgn}. Longitud de datos: {len(data)}")
        return None