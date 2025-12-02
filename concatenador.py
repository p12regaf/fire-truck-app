import os
import glob
import re

def unir_sesiones():
    # Obtener el directorio actual donde se ejecuta el script
    current_dir = os.getcwd()
    print(f"--- Iniciando fusión de sesiones en: {current_dir} ---")

    # Buscar todos los archivos .txt
    files = glob.glob("*.txt")
    
    # Diccionario para agrupar archivos por su "nombre base"
    # El nombre base sería: TIPO_DOBACKXXX_YYYYMMDD (sin el _S1, _S2...)
    grupos = {}

    # Regex para detectar el formato: TIPO_DOBACKXXX_YYYYMMDD_S#.txt
    # Ejemplo: ESTABILIDAD_DOBACK001_20231027_S1.txt
    patron = re.compile(r'(.+_.+_\d{8})_S\d+\.txt')

    for archivo in files:
        match = patron.match(archivo)
        if match:
            base_name = match.group(1) # Ej: ESTABILIDAD_DOBACK001_20231027
            if base_name not in grupos:
                grupos[base_name] = []
            grupos[base_name].append(archivo)
        else:
            # Ignorar archivos que no cumplan el patrón (como los _RealTime o archivos ya unidos)
            pass

    if not grupos:
        print("No se encontraron archivos de sesiones (con formato ..._S#.txt) para unir.")
        return

    # Procesar cada grupo
    for base_name, lista_archivos in grupos.items():
        # Ordenar los archivos para que las sesiones queden en orden (S1, S2, S10...)
        # Usamos una función lambda para extraer el número de sesión y ordenar numéricamente
        try:
            lista_archivos.sort(key=lambda x: int(re.search(r'_S(\d+)\.txt', x).group(1)))
        except AttributeError:
            lista_archivos.sort() # Fallback al orden alfabético si falla

        nombre_salida = f"{base_name}.txt"
        
        print(f"\nProcesando: {nombre_salida}")
        print(f" -> Encontradas {len(lista_archivos)} partes: {lista_archivos}")

        try:
            with open(nombre_salida, 'w', encoding='utf-8') as outfile:
                for i, filename in enumerate(lista_archivos):
                    with open(filename, 'r', encoding='utf-8') as infile:
                        contenido = infile.read()
                        
                        # OPCIONAL: Si quieres quitar las cabeceras de las sesiones siguientes
                        # para que solo quede la primera, descomenta las siguientes líneas:
                        # if i > 0:
                        #     # Suponiendo que la cabecera son las primeras 2 lineas
                        #     lineas = contenido.splitlines(keepends=True)
                        #     contenido = "".join(lineas[2:]) 

                        outfile.write(contenido)
                        
                        # Asegurar que haya un salto de línea entre archivos si no lo tienen
                        if contenido and not contenido.endswith('\n'):
                            outfile.write('\n')
            
            print(f" [OK] Archivo creado exitosamente: {nombre_salida}")

        except Exception as e:
            print(f" [ERROR] No se pudo crear {nombre_salida}: {e}")

    print("\n--- Proceso finalizado ---")
    input("Presiona ENTER para salir...")

if __name__ == "__main__":
    unir_sesiones()