"""Interfaz local para RDA ARIS. Tkinter + hilo de trabajo + logs persistentes."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from workflow import ejecutar_flujo_1
from workflow_prod import ejecutar_flujo_2

BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "rda_subir_a_prod"
PROFILE = BASE / "chrome_profile"
LOGS = BASE / "logs"
RESULTS = BASE / "resultados"
CONFIG = BASE / "config.json"


def configurar_logs(events: queue.Queue):
    LOGS.mkdir(parents=True, exist_ok=True)
    filename = LOGS / f"run_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
    handler = logging.FileHandler(filename, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return filename, handler


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RDA · ARIS | Preparación y pase a producción")
        self.root.geometry("860x700")
        self.root.minsize(680, 560)
        self.events = queue.Queue()
        self.busy = False
        self.link1 = tk.StringVar()
        self.link2 = tk.StringVar()
        self.link3 = tk.StringVar()
        self.simular = tk.BooleanVar(value=True)
        self.state = tk.StringVar(value="Sin sesión validada")
        self._draw()
        self._load()
        self.root.after(120, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _draw(self):
        p = ttk.Frame(self.root, padding=18)
        p.pack(fill="both", expand=True)
        ttk.Label(p, text="RDA · ARIS", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(p, text="Flujo 1: preparar mapas | Flujo 2: publicar modelos revisados", wraplength=810).pack(anchor="w", pady=(0, 14))
        ttk.Label(p, text="Link 1 · Flujo 1: origen importado | Flujo 2: destino en producción (atencion_r)").pack(anchor="w")
        ttk.Entry(p, textvariable=self.link1).pack(fill="x", pady=(4, 10))
        ttk.Label(p, text="Link 2 · Flujo 2: carpeta de procesos preparados y revisados").pack(anchor="w")
        ttk.Entry(p, textvariable=self.link2).pack(fill="x", pady=(4, 10))
        ttk.Label(p, text="Enlace adicional · Link 3 (reservado)").pack(anchor="w")
        ttk.Entry(p, textvariable=self.link3).pack(fill="x", pady=(4, 10))
        ttk.Label(p, textvariable=self.state, foreground="#165c9c").pack(anchor="w", pady=(4, 8))
        actions = ttk.Frame(p)
        actions.pack(fill="x", pady=8)
        ttk.Checkbutton(
            actions, text="Simulación (sin modificar ARIS)", variable=self.simular
        ).pack(side="left", padx=(0, 10))
        self.validate_btn = ttk.Button(
            actions, text="Validar flujo 1",
            command=lambda: self._start(True, flujo=1)
        )
        self.validate_btn.pack(side="left", padx=(0, 10))
        self.execute_btn = ttk.Button(
            actions, text="Ejecutar flujo 1",
            command=lambda: self._start(self.simular.get(), flujo=1)
        )
        self.execute_btn.pack(side="left")
        actions2 = ttk.Frame(p)
        actions2.pack(fill="x", pady=(0, 6))
        self.validate_prod_btn = ttk.Button(
            actions2, text="Validar flujo 2 (sin cambios)",
            command=lambda: self._start(True, flujo=2)
        )
        self.validate_prod_btn.pack(side="left", padx=(0, 10))
        self.execute_prod_btn = ttk.Button(
            actions2, text="Ejecutar flujo 2 · Producción",
            command=lambda: self._start(self.simular.get(), flujo=2)
        )
        self.execute_prod_btn.pack(side="left")
        log_actions = ttk.Frame(p)
        log_actions.pack(fill="x", pady=(12, 5))
        ttk.Label(log_actions, text="Registro de ejecución").pack(side="left")
        ttk.Button(log_actions, text="Abrir logs", command=self._open_logs).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(log_actions, text="Abrir reportes JSON", command=self._open_results).pack(
            side="right"
        )
        self.console = scrolledtext.ScrolledText(p, height=18, state="disabled", font=("Consolas", 10))
        self.console.pack(fill="both", expand=True)
        ttk.Label(p, text=f"Perfil y reportes: {BASE}", wraplength=800).pack(anchor="w", pady=(8, 0))

    def _open_folder(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # Windows corporativo; no necesita admin
        except (AttributeError, OSError) as exc:
            messagebox.showerror("No se pudo abrir la carpeta", f"{folder}\n{exc}")

    def _open_logs(self):
        self._open_folder(LOGS)

    def _open_results(self):
        self._open_folder(RESULTS)

    def _save(self):
        BASE.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({
            "link1": self.link1.get(),
            "link2": self.link2.get(),
            "link3": self.link3.get(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self):
        if CONFIG.exists():
            try:
                cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
                for k in ("link1", "link2", "link3"):
                    getattr(self, k).set(cfg.get(k, ""))
            except (ValueError, OSError):
                pass

    def _log(self, message):
        logging.info(message)
        self.events.put(("log", message))

    def _start(self, simular: bool, flujo: int = 1):
        if self.busy:
            return
        try:
            from aris import validar_url
            url1 = validar_url(self.link1.get())
            url2 = validar_url(self.link2.get()) if flujo == 2 else None
            if flujo == 2 and url1 == url2:
                raise ValueError("Los enlaces de producción y origen son idénticos")
        except ValueError as exc:
            messagebox.showerror("Enlace inválido", str(exc))
            return
        if not simular:
            if flujo == 2:
                texto = (
                    "FLUJO 2: PASO A PRODUCCIÓN\n\n"
                    "El bot CREARÁ grupos N5 en el Link 1 y ejecutará "
                    "el informe de publicación con los modelos del Link 2.\n\n"
                    "En la resolución de conflictos, ARIS indica que "
                    "MODELOS, OBJETOS y CONEXIONES del ORIGEN "
                    "SOBREESCRIBEN los del DESTINO.\n\n"
                    "Se valida que la carpeta esté vacía y que la ruta del "
                    "asistente sea 04 PERU / 90 OTROS MODELOS / atencion_r / N5.\n"
                    "Sin reintentos automáticos. ¿Confirmas la ejecución REAL?"
                )
            else:
                texto = (
                    "Flujo 1 creará, copiará, moverá y renombrará carpetas "
                    "y lanzará informes. Sin reintentos automáticos.\n\n¿Ejecutar?"
                )
            if not messagebox.askyesno("Confirmar cambios reales en ARIS", texto):
                return
        self._save()
        self.busy = True
        self.state.set(
            f"Flujo {flujo}: " +
            ("validando y mapeando..." if simular else "ejecutando cambios reales...")
        )
        for b in (
            self.validate_btn, self.execute_btn,
            self.validate_prod_btn, self.execute_prod_btn
        ):
            b.configure(state="disabled")
        t = threading.Thread(
            target=self._worker, args=(flujo, url1, url2, simular),
            daemon=True
        )
        t.start()

    def _worker(self, flujo: int, url1: str, url2: str | None, simular: bool):
        path, handler = configurar_logs(self.events)
        self._log(f"[FLUJO {flujo}] Log guardado en {path}")
        try:
            if flujo == 1:
                result = ejecutar_flujo_1(
                    url1, PROFILE, RESULTS, self._log, simular=simular
                )
            else:
                result = ejecutar_flujo_2(
                    url1, url2, PROFILE, RESULTS,
                    self._log, simular=simular
                )
            self.events.put(("done", str(result)))
        except Exception as exc:
            self._log(f"Error inesperado: {type(exc).__name__}: {exc}")
            self.events.put(("error", str(exc)))
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def _drain(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                if kind == "log":
                    self.console.configure(state="normal")
                    self.console.insert("end", val + "\n")
                    self.console.see("end")
                    self.console.configure(state="disabled")
                    if val.startswith(("[MAPEO]", "[COPIAS]", "[COPIA]", "[RENOMBRADO]", "[PROD]")):
                        self.state.set(val[:160])
                else:
                    self.busy = False
                    for b in (
                        self.validate_btn, self.execute_btn,
                        self.validate_prod_btn, self.execute_prod_btn
                    ):
                        b.configure(state="normal")
                    self.state.set("Finalizado; revisa reporte JSON" if kind == "done" else "Error")
                    if kind == "done":
                        self._append_report(val)
                    else:
                        messagebox.showerror("RDA ARIS", val)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _append_report(self, filename):
        self.console.configure(state="normal")
        self.console.insert("end", f"\nREPORTE: {filename}\n")
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
            if data.get("estado") == "interrumpido":
                if data.get("simulacion") is True or data.get("modo") == "simulacion":
                    self.state.set(
                        "Validación interrumpida, SIN cambios en ARIS. Ver logs y JSON."
                    )
                else:
                    self.state.set(
                        "Ejecución interrumpida: puede haber cambios parciales. "
                        "Ver logs y JSON."
                    )
            elif data.get("estado") == "completado_con_errores":
                self.state.set("Completado con incidencias. Ver logs y JSON.")
            elif data.get("estado") == "completado":
                self.state.set(
                    f"Flujo {data.get('flujo', 1)} completado y registrado."
                )
            elif data.get("estado") == "simulacion_correcta":
                self.state.set("Validación finalizada sin cambios.")
            self.console.insert(
                "end",
                f"ESTADO: {data['estado']}\n"
                f"COPIAS: {data.get('copias', {})}\n"
                f"PUBLICACIÓN: {data.get('registros', []) if data.get('flujo') == 2 else ''}\n"
                f"RESUMEN: {data.get('resumen', {})}\n"
                f"ERRORES: {data.get('errores_globales', [])}\n"
            )
        except Exception as exc:
            self.console.insert("end", f"No fue posible leer reporte: {exc}\n")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _close(self):
        if self.busy:
            messagebox.showwarning("Ejecución activa", "No cierres la ventana durante una operación ARIS. Espera el reporte.")
            return
        self._save()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
