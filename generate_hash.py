import getpass
from werkzeug.security import generate_password_hash

def main():
    print("--- Generador de Contraseñas Seguras para Administrador ---")
    print("Esta herramienta convertirá tu contraseña en un hash cifrado para que puedas guardarla de forma segura en el archivo .env\n")
    
    pwd1 = getpass.getpass("Ingresa la nueva contraseña: ")
    pwd2 = getpass.getpass("Confirma la nueva contraseña: ")
    
    if pwd1 != pwd2:
        print("\n[ERROR] Las contraseñas no coinciden. Intenta de nuevo.")
        return
        
    if len(pwd1) < 6:
        print("\n[ERROR] La contraseña debe tener al menos 6 caracteres por seguridad.")
        return
        
    hash_result = generate_password_hash(pwd1, method="pbkdf2:sha256")
    
    print("\n¡Hash generado correctamente!")
    print("-" * 50)
    print(f"ADMIN_PASSWORD_HASH={hash_result}")
    print("-" * 50)
    print("\nInstrucciones:")
    print("1. Abre tu archivo '.env'")
    print("2. REEMPLAZA tu línea de 'ADMIN_PASSWORD' actual con la copia exacta de la línea generada arriba.")
    print("3. Reinicia tu servidor Flask.")

if __name__ == "__main__":
    main()
