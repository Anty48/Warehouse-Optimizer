#!/usr/bin/env python3
"""
Optimizer — Ryzen 7 edition
Lanza hasta MAX_WORKERS procesos en paralelo real (Popen, no run).
En cuanto uno termina, arranca otro inmediatamente.
Con srand por PID en el .exe, cada proceso tiene semilla única.
"""

import subprocess
import threading
import shutil
import time
import re
import sys
import csv
from pathlib import Path
import os

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
EXE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "warehouse_optimizer.exe")
RESULT_CSV     = "result.csv"
BEST_CSV       = "best_output.csv"
OUTPUT_CSV     = "output.csv"
TIME_LIMIT_SEC = 29
MAX_WORKERS    = 12   # = número de núcleos físicos del Ryzen 7
# ──────────────────────────────────────────────

Q_PATTERN = re.compile(r"Q\s*=\s*([\d.eE+\-]+)")

best_q      = float("inf")
best_q_lock = threading.Lock()
run_count   = 0
run_lock    = threading.Lock()
stop_event  = threading.Event()

WORK_DIR    = Path(EXE_PATH).parent.resolve()
CSV_PATH    = WORK_DIR / RESULT_CSV
BEST_PATH   = WORK_DIR / BEST_CSV
OUTPUT_PATH = WORK_DIR / OUTPUT_CSV


def parse_q(text: str):
    match = Q_PATTERN.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def create_output_csv():
    """Lee best_output.csv y genera output.csv con columnas id, X, Y, GapAngle_deg (cols 1,2,3,5)."""
    if not BEST_PATH.exists():
        return
    try:
        with open(BEST_PATH, newline="", encoding="utf-8") as infile:
            reader = csv.reader(infile)
            rows = list(reader)

        if not rows:
            return

        # Extraer columnas 0,1,2,4 (índices) → id, X, Y, GapAngle_deg (columnas 1,2,3,5)
        COLS = [0, 1, 2, 4]

        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as outfile:
            writer = csv.writer(outfile)
            for row in rows:
                # Saltar filas que no tengan suficientes columnas
                if len(row) >= 5:
                    writer.writerow([row[i] for i in COLS])

    except Exception as e:
        print(f"  [WARN] No se pudo crear output.csv: {e}")


def process_output(stdout_bytes, stderr_bytes, thread_index):
    """Parsea el output y actualiza el mejor Q. Thread-safe."""
    global best_q, run_count

    stdout = stdout_bytes.decode("utf-8", errors="ignore") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="ignore") if stderr_bytes else ""
    q = parse_q(stdout + stderr)

    with run_lock:
        run_count += 1
        current_run = run_count

    if q is None:
        print(f"  Run #{current_run:>4} [hilo {thread_index}] -- Q no encontrado")
        return

    with best_q_lock:
        if q < best_q:
            best_q = q
            if CSV_PATH.exists():
                shutil.copy2(str(CSV_PATH), str(BEST_PATH))
                create_output_csv()
                print(f"  Run #{current_run:>4} [hilo {thread_index}] -- *** NUEVO MEJOR Q = {q:.6f}  -> {BEST_PATH.name}  -> {OUTPUT_PATH.name}")
            else:
                print(f"  Run #{current_run:>4} [hilo {thread_index}] -- *** NUEVO MEJOR Q = {q:.6f}  (sin output.csv)")
        else:
            print(f"  Run #{current_run:>4} [hilo {thread_index}] -- Q = {q:.6f}  (mejor: {best_q:.6f})")


def worker(thread_index: int):
    """
    Cada hilo gestiona su propio proceso con Popen.
    En cuanto el proceso termina, lanza el siguiente SIN esperar a los demás.
    """
    exe = str(Path(EXE_PATH).resolve())
    cwd = str(WORK_DIR)

    while not stop_event.is_set():
        try:
            proc = subprocess.Popen(
                [exe],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError:
            print(f"[ERROR] No se encuentra el ejecutable: {EXE_PATH}")
            stop_event.set()
            return
        except Exception as e:
            print(f"[WARN] Hilo {thread_index}: {e}")
            continue

        # Esperar a que termine comprobando stop_event cada 0.1s
        # Si se acabó el tiempo, matar el proceso limpiamente
        while True:
            try:
                stdout_b, stderr_b = proc.communicate(timeout=0.1)
                break  # proceso terminó
            except subprocess.TimeoutExpired:
                if stop_event.is_set():
                    proc.kill()
                    proc.communicate()  # limpiar buffers
                    return
                # sigue corriendo, esperamos más

        process_output(stdout_b, stderr_b, thread_index)


def main():
    global EXE_PATH, WORK_DIR, CSV_PATH, BEST_PATH, OUTPUT_PATH

    exe = sys.argv[1] if len(sys.argv) > 1 else EXE_PATH
    EXE_PATH    = exe
    WORK_DIR    = Path(exe).parent.resolve()
    CSV_PATH    = WORK_DIR / RESULT_CSV
    BEST_PATH   = WORK_DIR / BEST_CSV
    OUTPUT_PATH = WORK_DIR / OUTPUT_CSV

    if not Path(exe).exists():
        print(f"[ERROR] Ejecutable no encontrado: {exe}")
        sys.exit(1)

    print("=" * 60)
    print(f"  Ejecutable : {Path(exe).resolve()}")
    print(f"  Duracion   : {TIME_LIMIT_SEC}s")
    print(f"  Procesos   : {MAX_WORKERS}  (uno por nucleo)")
    print("=" * 60)

    threads = []
    for i in range(MAX_WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    start = time.time()
    try:
        while time.time() - start < TIME_LIMIT_SEC:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[INFO] Interrumpido por el usuario.")

    print("\n[INFO] Tiempo agotado, cerrando procesos...")
    stop_event.set()

    for t in threads:
        t.join(timeout=20)

    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"  Tiempo transcurrido : {elapsed:.1f}s")
    print(f"  Ejecuciones totales : {run_count}")
    print(f"  Mejor Q encontrado  : {best_q:.6f}")

    if BEST_PATH.exists():
        shutil.copy2(str(BEST_PATH), str(CSV_PATH))
        print(f"  output.csv final    : restaurado desde {BEST_PATH.name}")
    else:
        print("  [WARN] No se guardo ningun output.csv")

    if OUTPUT_PATH.exists():
        print(f"  output.csv final    : {OUTPUT_PATH.name}  (id, X, Y, GapAngle_deg)")

    print("=" * 60)


if __name__ == "__main__":
    main()