import tkinter as tk
from tkinter import ttk

class StatusView(ttk.Frame):
    """
    Una vista de la GUI que muestra el estado general del sistema y los últimos datos recibidos.
    """
    def __init__(self, parent):
        super().__init__(parent, padding="10")
        
        self.data_labels = {}
        self.status_labels = {}

        # --- Sección de Estado de Servicios ---
        status_frame = ttk.LabelFrame(self, text="Estado de los Servicios", padding="10")
        status_frame.pack(fill=tk.X, pady=10)
        self.status_container = status_frame

        # --- Sección de Últimos Datos ---
        data_frame = ttk.LabelFrame(self, text="Últimos Datos Recibidos", padding="10")
        data_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.data_container = data_frame

    def update_data(self, latest_data: dict, service_status: dict):
        """Recibe datos del GuiController y actualiza las etiquetas."""
        
        # Actualizar estado de servicios
        for service_name, status in service_status.items():
            if service_name not in self.status_labels:
                frame = ttk.Frame(self.status_container)
                ttk.Label(frame, text=f"{service_name}:").pack(side=tk.LEFT)
                self.status_labels[service_name] = ttk.Label(frame, text="Desconocido", width=10)
                self.status_labels[service_name].pack(side=tk.LEFT, padx=5)
                frame.pack(anchor="w")
            
            self.status_labels[service_name].config(text=status, foreground="green" if status == "Running" else "red")
            
        # Actualizar últimos datos
        for data_type, packet in latest_data.items():
            if data_type not in self.data_labels:
                self.data_labels[data_type] = ttk.Label(self.data_container, text="", justify=tk.LEFT)
                self.data_labels[data_type].pack(anchor="w", pady=2)
            
            timestamp = packet.get('timestamp', 'N/A').split('.')[0] # Quitar microsegundos
            data_str = ", ".join(f"{k}: {v}" for k, v in packet.get('data', {}).items())
            self.data_labels[data_type].config(text=f"[{data_type.upper()}] @ {timestamp} -> {data_str}")