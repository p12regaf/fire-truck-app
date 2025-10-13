#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# --- CONFIGURACIÓN ---
# Nombre del archivo Markdown que se generará
OUTPUT_FILENAME = "codigo_completo.md"

# Extensiones de los archivos que queremos buscar
TARGET_EXTENSIONS = ('.py', '.service', '.txt', '.csv', '.dbc', '.sh', '.json', '.yaml', '.yml', '.html.j2', '.sh')

# Lista de carpetas raíz que quieres escanear (búsqueda recursiva).
# Usa '.' para escanear todo desde la carpeta actual.
TARGET_DIRECTORIES = [
    '.' 
]

# ¡NUEVO! Lista de carpetas que quieres EXCLUIR de la búsqueda.
# El script ignorará por completo estas carpetas y todo su contenido.
# Es ideal para carpetas de entornos virtuales, repositorios git, cachés, etc.
EXCLUDED_DIRECTORIES = [
    './.venv',          # Entorno virtual de Python
    './venv',           # Otro nombre común para entorno virtual
    './.git',           # Carpeta del repositorio Git
    './__pycache__',    # Carpetas de caché de Python
    './node_modules'    # Carpeta de dependencias de Node.js
]
# ---------------

def get_markdown_language(filename):
    """Devuelve el identificador de lenguaje para el bloque de código Markdown."""
    if filename.endswith('.py'):
        return 'python'
    if filename.endswith('.service'):
        return 'ini'
    return 'text'

def main():
    """
    Función principal que busca archivos, respetando las exclusiones, y escribe el Markdown.
    """
    try:
        # Normalizamos las rutas de exclusión para una comparación más fiable
        # os.path.normpath elimina './' y convierte '/' a '\' en Windows, etc.
        normalized_excluded_dirs = [os.path.normpath(d) for d in EXCLUDED_DIRECTORIES]

        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as md_file:
            print(f"Generando el archivo: {OUTPUT_FILENAME}")
            print(f"Carpetas a escanear: {', '.join(TARGET_DIRECTORIES)}")
            print(f"Carpetas a excluir: {', '.join(EXCLUDED_DIRECTORIES)}")
            print(f"Extensiones buscadas: {', '.join(TARGET_EXTENSIONS)}\n")
            
            found_files = False
            for target_dir in TARGET_DIRECTORIES:
                if not os.path.isdir(target_dir):
                    print(f"¡Atención! La carpeta de inicio '{target_dir}' no existe y será omitida.")
                    continue

                for root, dirs, files in os.walk(target_dir, topdown=True):
                    # --- LÓGICA DE EXCLUSIÓN ---
                    # Modificamos la lista 'dirs' en el momento para que os.walk
                    # no entre en los directorios excluidos. Es la forma más eficiente.
                    dirs[:] = [d for d in dirs if os.path.normpath(os.path.join(root, d)) not in normalized_excluded_dirs]
                    
                    for filename in files:
                        if filename.endswith(TARGET_EXTENSIONS):
                            full_path = os.path.join(root, filename)
                            found_files = True
                            
                            print(f"  -> Añadiendo {full_path}...")
                            
                            md_file.write(f"## Archivo: `{full_path}`\n\n")
                            lang = get_markdown_language(filename)
                            md_file.write(f"```{lang}\n")

                            try:
                                with open(full_path, 'r', encoding='utf-8', errors='ignore') as src_file:
                                    content = src_file.read()
                                    md_file.write(content)
                            except Exception as e:
                                error_message = f"Error al leer el archivo: {e}"
                                md_file.write(error_message)
                                print(f"  -> ¡Error! No se pudo leer el archivo {full_path}: {e}")

                            md_file.write("\n```\n\n")
            
            if not found_files:
                 print("\nNo se encontraron archivos con las extensiones especificadas en las carpetas de destino (después de aplicar exclusiones).")
            
            print(f"\n¡Proceso completado! El archivo '{OUTPUT_FILENAME}' ha sido generado.")

    except IOError as e:
        print(f"Error: No se pudo crear o escribir en el archivo de salida '{OUTPUT_FILENAME}'.")
        print(f"Detalle del error: {e}")

if __name__ == "__main__":
    main()