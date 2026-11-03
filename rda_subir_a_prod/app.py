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
        ttk.Label(p, text="Flujo 1: preparar modelos y generar mapas | Flujo 2: pase a producción (pendiente)").pack(anchor="w", pady=(0, 14))
        ttk.Label(p, text="Carpeta de diagramas recién importados · Link 1").pack(anchor="w")
        ttk.Entry(p, textvariable=self.link1).pack(fill="x", pady=(4, 10))
        ttk.Label(p, text="Destino de producción · Link 2 (reservado para flujo 2)").pack(anchor="w")
        ttk.Entry(p, textvariable=self.link2).pack(fill="x", pady=(4, 10))
        ttk.Label(p, text="Enlace adicional · Link 3 (reservado)").pack(anchor="w")
        ttk.Entry(p, textvariable=self.link3).pack(fill="x", pady=(4, 10))
        ttk.Label(p, textvariable=self.state, foreground="#165c9c").pack(anchor="w", pady=(4, 8))
        actions = ttk.Frame(p)
        actions.pack(fill="x", pady=8)
        self.validate_btn = ttk.Button(actions, text="Validar sesión y mapear", command=self._preflight)
        self.validate_btn.pack(side="left", padx=(0, 10))
        ttk.Checkbutton(actions, text="Simulación (sin modificar ARIS)", variable=self.simular).pack(side="left", padx=(0, 10))
        self.execute_btn = ttk.Button(actions, text="Ejecutar flujo 1", command=self._execute)
        self.execute_btn.pack(side="left", padx=(0, 10))
        ttk.Button(actions, text="Flujo 2 · Próximamente", state="disabled").pack(side="left")
        ttk.Label(p, text="Registro de ejecución").pack(anchor="w", pady=(12, 5))
        self.console = scrolledtext.ScrolledText(p, height=18, state="disabled", font=("Consolas", 10))
        self.console.pack(fill="both", expand=True)
        ttk.Label(p, text=f"Perfil y reportes: {BASE}", wraplength=800).pack(anchor="w", pady=(8, 0))

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

    def _start(self, simular: bool):
        if self.busy:
            return
        try:
            from aris import validar_url
            url = validar_url(self.link1.get())
        except ValueError as exc:
            messagebox.showerror("Link 1 inválido", str(exc))
            return
        if not simular:
            if not messagebox.askyesno("Confirmar cambios reales",
                     "Este flujo CREARÁ, COPIARÁ, CORTARÁ, PEGARÁ y RENOMBRARÁ carpetas, "
                     "y lanzará informes en ARIS. No tendrá reintentos automáticos.\n\n¿Ejecutar?"):
                return
        self._save()
        self.busy = True
        self.state.set("Validando ARIS..." if simular else "Ejecutando cambios reales...")
        self.validate_btn.configure(state="disabled")
        self.execute_btn.configure(state="disabled")
        t = threading.Thread(target=self._worker, args=(url, simular), daemon=True)
        t.start()

    def _worker(self, url, simular):
        path, handler = configurar_logs(self.events)
        self._log(f"Log guardado en {path}")
        try:
            result = ejecutar_flujo_1(url, PROFILE, RESULTS, self._log, simular=simular)
            self.events.put(("done", str(result)))
        except Exception as exc:
            self._log(f"Error inesperado: {type(exc).__name__}: {exc}")
            self.events.put(("error", str(exc)))
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def _preflight(self):
        self._start(simular=True)

    def _execute(self):
        self._start(simular=self.simular.get())

    def _drain(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                if kind == "log":
                    self.console.configure(state="normal")
                    self.console.insert("end", val + "\n")
                    self.console.see("end")
                    self.console.configure(state="disabled")
                else:
                    self.busy = False
                    self.validate_btn.configure(state="normal")
                    self.execute_btn.configure(state="normal")
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
            self.console.insert("end", f"ESTADO: {data['estado']}\nRESUMEN: {data.get('resumen', {})}\n")
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
