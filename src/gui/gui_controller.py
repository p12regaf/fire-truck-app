class GuiController:
    """
    Intermediario entre la lógica de la aplicación (AppController) y la GUI (MainWindow y sus vistas).
    """
    def __init__(self, app_controller, main_window):
        self.app_controller = app_controller
        self.main_window = main_window

    def update_all_views(self):
        """Pide los datos más recientes al backend y actualiza todas las vistas."""
        latest_data = self.app_controller.get_latest_data()
        service_status = self.app_controller.get_service_status()

        # Actualizar la vista de estado
        if self.main_window.status_view:
            self.main_window.status_view.update_data(latest_data, service_status)
        
        # Aquí se llamarían a los métodos de actualización de otras vistas
        # ej. self.main_window.can_view.update_data(...)