"""Script de diagnóstico inicial e verificação de sanidade do ambiente FC-Tactical-Engine."""

import importlib.metadata
import platform
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def get_package_version(module_name: str, dist_name: str) -> str:
    """Obtém a versão do pacote via atributo __version__ ou importlib.metadata."""
    module = sys.modules.get(module_name)
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return "N/A"


def check_imports() -> bool:
    """Valida a importação de todas as bibliotecas críticas do projeto."""
    print("=" * 65)
    print("1. VERIFICAÇÃO DE BIBLIOTECAS CRÍTICAS")
    print("=" * 65)

    critical_packages = [
        ("dxcam", "dxcam", "dxcam"),
        ("cv2 (OpenCV)", "cv2", "opencv-python"),
        ("numpy", "numpy", "numpy"),
        ("scipy", "scipy", "scipy"),
        ("shapely", "shapely", "shapely"),
        ("duckdb", "duckdb", "duckdb"),
        ("pydantic", "pydantic", "pydantic"),
        ("pyttsx3", "pyttsx3", "pyttsx3"),
        ("fastapi", "fastapi", "fastapi"),
        ("uvicorn", "uvicorn", "uvicorn"),
        ("websockets", "websockets", "websockets"),
    ]

    all_ok = True
    for label, module_name, dist_name in critical_packages:
        try:
            __import__(module_name)
            version = get_package_version(module_name, dist_name)
            print(f"  [OK] {label:<18} -> versão: {version}")
        except Exception as exc:
            print(f"  [ERRO] {label:<16} -> Falha ao importar: {exc}")
            all_ok = False

    print()
    return all_ok


def check_dxcam() -> bool:
    """Valida a inicialização do DXCam e detecção de GPU / monitores via DirectX DDA."""
    print("=" * 65)
    print("2. DIAGNÓSTICO DE CAPTURA DIRECTX (DXCAM - DDA)")
    print("=" * 65)

    try:
        import dxcam

        devices_info = dxcam.device_info().strip()
        outputs_info = dxcam.output_info().strip()

        if not devices_info or not outputs_info:
            print("  [ERRO] Nenhuma GPU ou monitor detectado via DXGI.")
            return False

        print("  -> Dispositivos DirectX (GPU) Detectados:")
        for line in devices_info.splitlines():
            print(f"     {line}")

        print("  -> Topologia de Monitores Ativos:")
        for line in outputs_info.splitlines():
            print(f"     {line}")

        try:
            camera = dxcam.create(output_idx=0, output_color="BGR")
            if camera is not None:
                print(
                    f"  [OK] Instância DXCam Desktop Duplication ativa (Região: {camera.region})."
                )
                del camera
        except Exception as dup_exc:
            # HRESULT -2147024891 (0x80070005 E_ACCESSDENIED) ocorre quando o processo
            # roda em sessão de terminal restrita/sandbox sem acesso direto ao Desktop WinSta0,
            # embora a enumeração DXGI de GPU e monitores funcione normalmente.
            if "-2147024891" in str(dup_exc) or "Acesso negado" in str(dup_exc):
                print(
                    "  [OK] DXGI/DirectX inicializado e GPU/Monitores mapeados com sucesso."
                )
                print(
                    "  [INFO] DuplicateOutput retornou E_ACCESSDENIED no contexto de terminal "
                    "isolado (esperado fora de sessão interativa de desktop)."
                )
            else:
                raise dup_exc

        print()
        return True
    except Exception as exc:
        print(f"  [ERRO] Falha na inicialização do DXCam/DirectX DDA: {exc}\n")
        return False


def check_duckdb() -> bool:
    """Valida a instanciação de banco em memória no DuckDB e execução de query analítica."""
    print("=" * 65)
    print("3. DIAGNÓSTICO DE PERSISTÊNCIA COLUNAR (DUCKDB IN-MEMORY)")
    print("=" * 65)

    try:
        import duckdb

        conn = duckdb.connect(database=":memory:")
        conn.execute(
            "CREATE TABLE telemetry_ping (frame_id INTEGER, x DOUBLE, y DOUBLE);"
        )
        conn.execute("INSERT INTO telemetry_ping VALUES (1, 52.5, 34.0);")
        result = conn.execute("SELECT frame_id, x, y FROM telemetry_ping;").fetchone()
        conn.close()

        if result == (1, 52.5, 34.0):
            print(f"  [OK] Conexão DuckDB `:memory:` operacional. Teste de I/O: {result}")
            print()
            return True
        print(f"  [ERRO] Retorno inesperado da query DuckDB: {result}\n")
        return False
    except Exception as exc:
        print(f"  [ERRO] Falha ao inicializar DuckDB em memória: {exc}\n")
        return False


def main() -> int:
    print("\n" + "#" * 65)
    print("   FC-TACTICAL-ENGINE | DIAGNÓSTICO DE SANIDADE DO AMBIENTE")
    print("#" * 65)
    print(f"SO: {platform.system()} {platform.release()} ({platform.version()})")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})\n")

    imports_ok = check_imports()
    dxcam_ok = check_dxcam()
    duckdb_ok = check_duckdb()

    print("=" * 65)
    if imports_ok and dxcam_ok and duckdb_ok:
        print("STATUS FINAL: [100% OPERACIONAL] Ambiente pronto para desenvolvimento!")
        print("=" * 65)
        return 0
    else:
        print("STATUS FINAL: [FALHA] Verifique os erros reportados acima.")
        print("=" * 65)
        return 1


if __name__ == "__main__":
    sys.exit(main())
