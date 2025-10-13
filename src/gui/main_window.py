import tkinter as tk
from tkinter import ttk
from .gui_controller import GuiController
from .views.status_view import StatusView

class MainWindow:
    def __init__(self, app_controller):
        self.app_controller = app_controller
        self.is_running_flag = False

        self.root = tk.Tk()
        self.root.title("fire-truck-app Control Panel")
        self.root.geometry("800x600")

        self.gui_controller = GuiController(app_controller, self)
        
        self.create_widgets()

        # Iniciar el ciclo de actualización de la UI
        self.update_ui()
        
        # Manejar el cierre de la ventana
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def create_widgets(self):
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # Aquí se podrían añadir más vistas en un Notebook (pestañas) o en un panel lateral
        self.status_view = StatusView(self.main_frame)
        self.status_view.pack(fill=tk.BOTH, expand=True)

    def update_ui(self):
        """Llama al controlador para que actualice los datos de las vistas."""
        if not self.is_running_flag:
            return
        
        self.gui_controller.update_all_views()
        # Reprogramar la próxima actualización en 1 segundo (1000 ms)
        self.root.after(1000, self.update_ui)

    def run(self):
        """Inicia el bucle principal de la GUI."""
        self.is_running_flag = True
        self.root.mainloop()

    def close(self):
        """Cierra la ventana de la GUI."""
        if self.is_running_flag:
            self.is_running_flag = False
            self.root.destroy()
            
    def is_running(self) -> bool:
        return self.is_running_flag