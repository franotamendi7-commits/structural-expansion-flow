import json, os, secrets, shutil
from datetime import datetime, date, timedelta

RUTA_INVESTORS = "/Users/franciscootamendi/ai-agents-v3/investors.json"
RUTA_BACKUP = "/Users/franciscootamendi/ai-agents-v3/backups"

class PortfolioManager:
    def __init__(self, investors_file=RUTA_INVESTORS):
        self.investors_file = investors_file
        self.inversores = self._cargar()
        os.makedirs(RUTA_BACKUP, exist_ok=True)

    def _cargar(self):
        if not os.path.exists(self.investors_file):
            return []
        with open(self.investors_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for inv in data.get("inversores", []):
            inv.setdefault("usuario", inv["id"])
            inv.setdefault("password", "changeme")
            inv.setdefault("fecha_solicitud_retiro", None)
            inv.setdefault("porcentaje_retiro", None)
            inv.setdefault("monto_retiro_solicitado", None)
            inv.setdefault("transacciones", [])
        return data.get("inversores", [])

    def _guardar(self):
        if os.path.exists(self.investors_file):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"investors_{timestamp}.json"
            shutil.copy2(self.investors_file, os.path.join(RUTA_BACKUP, backup_name))
        with open(self.investors_file, "w", encoding="utf-8") as f:
            json.dump({"inversores": self.inversores}, f, indent=2, ensure_ascii=False)

    def _registrar_transaccion(self, inv, tipo, monto, capital_antes, capital_despues):
        inv.setdefault("transacciones", [])
        inv["transacciones"].append({
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": tipo,
            "monto": round(monto, 2),
            "capital_antes": round(capital_antes, 2),
            "capital_despues": round(capital_despues, 2)
        })

    def login(self, usuario, password):
        for inv in self.inversores:
            if inv.get("usuario") == usuario and inv.get("password") == password:
                return inv
        return None

    def cambiar_password(self, inversor_id, nuevo_password):
        inv = next((i for i in self.inversores if i["id"] == inversor_id), None)
        if inv:
            inv["password"] = nuevo_password
            self._guardar()
            return True
        return False

    def ventana_retiro_abierta(self):
        hoy = date.today()
        if hoy.month == 12:
            ultimo = date(hoy.year, 12, 31)
        else:
            ultimo = date(hoy.year, hoy.month + 1, 1) - timedelta(days=1)
        inicio_ventana = ultimo - timedelta(days=2)
        return hoy >= inicio_ventana

    def calcular_participaciones(self):
        total = sum(i["capital_actual"] for i in self.inversores)
        return [{"id": i["id"], "name": i["name"], "capital_actual": i["capital_actual"],
                 "porcentaje": round(i["capital_actual"]/total*100,2) if total else 0} for i in self.inversores]

    def actualizar_balances(self, pnl_global):
        parts = self.calcular_participaciones()
        pct = {p["id"]: p["porcentaje"] for p in parts}
        for i in self.inversores:
            antes = i["capital_actual"]
            proporcion = pnl_global * pct.get(i["id"], 0) / 100
            i["capital_actual"] += proporcion
            if i["capital_actual"] > i["high_water_mark"]:
                i["high_water_mark"] = i["capital_actual"]
            self._registrar_transaccion(i, "rentabilidad", proporcion, antes, i["capital_actual"])
        self._guardar()

    def registrar_deposito(self, nombre, monto, inversor_id=None, usuario=None, password=None):
        if inversor_id:
            inv = next((i for i in self.inversores if i["id"] == inversor_id), None)
            if inv:
                antes = inv["capital_actual"]
                inv["capital_invertido"] += monto
                inv["capital_actual"] += monto
                if inv["capital_actual"] > inv["high_water_mark"]:
                    inv["high_water_mark"] = inv["capital_actual"]
                self._registrar_transaccion(inv, "deposito", monto, antes, inv["capital_actual"])
                self._guardar()
                return inv
        new_id = "inv_" + secrets.token_hex(8)
        nuevo = {
            "id": new_id,
            "name": nombre,
            "usuario": usuario or new_id,
            "password": password or "changeme",
            "capital_invertido": monto,
            "capital_actual": monto,
            "high_water_mark": monto,
            "fecha_ingreso": str(date.today()),
            "retiro_pendiente": False,
            "fecha_solicitud_retiro": None,
            "porcentaje_retiro": None,
            "monto_retiro_solicitado": None,
            "transacciones": []
        }
        self._registrar_transaccion(nuevo, "deposito", monto, 0, monto)
        self.inversores.append(nuevo)
        self._guardar()
        return nuevo

    def ajustar_capital(self, inversor_id, nuevo_capital):
        inv = next((i for i in self.inversores if i["id"] == inversor_id), None)
        if not inv: return None
        antes = inv["capital_actual"]
        diferencia = nuevo_capital - antes
        inv["capital_actual"] = nuevo_capital
        if nuevo_capital > inv["high_water_mark"]:
            inv["high_water_mark"] = nuevo_capital
        self._registrar_transaccion(inv, "ajuste", diferencia, antes, nuevo_capital)
        self._guardar()
        return inv

    def eliminar_inversor(self, inversor_id):
        antes = len(self.inversores)
        self.inversores = [i for i in self.inversores if i["id"] != inversor_id]
        if len(self.inversores) < antes:
            self._guardar()
            return True
        return False

    def procesar_retiro(self, inversor_id, porcentaje=100):
        inv = next((i for i in self.inversores if i["id"] == inversor_id), None)
        if not inv: return None
        if not self.ventana_retiro_abierta():
            return {'error': 'La ventana de retiro no está abierta. Solo se puede retirar los últimos 3 días del mes.'}

        capital_actual = inv["capital_actual"]
        hwm = inv["high_water_mark"]
        monto_bruto = capital_actual * (porcentaje / 100)

        ganancia_total = max(0.0, capital_actual - hwm)
        ganancia_proporcional = ganancia_total * (porcentaje / 100)
        comision_gestion = ganancia_proporcional * 0.50
        comision_salida = monto_bruto * 0.005
        total_recibir = monto_bruto - comision_gestion - comision_salida

        inv["capital_actual"] -= monto_bruto
        inv["high_water_mark"] -= hwm * (porcentaje / 100)
        inv["retiro_pendiente"] = True
        inv["fecha_solicitud_retiro"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        inv["porcentaje_retiro"] = porcentaje
        inv["monto_retiro_solicitado"] = total_recibir

        self._registrar_transaccion(inv, "retiro", monto_bruto, capital_actual, inv["capital_actual"])
        self._guardar()

        return {
            "id": inversor_id,
            "name": inv["name"],
            "porcentaje_retirado": porcentaje,
            "monto_bruto": monto_bruto,
            "capital_actual": inv["capital_actual"],
            "high_water_mark": inv["high_water_mark"],
            "ganancia_proporcional": ganancia_proporcional,
            "comision_gestion": comision_gestion,
            "comision_salida": comision_salida,
            "total_recibir": total_recibir,
            "retiro_pendiente": True
        }

    def completar_retiro(self, inversor_id):
        inv = next((i for i in self.inversores if i["id"] == inversor_id), None)
        if not inv: return False
        inv["retiro_pendiente"] = False
        inv["fecha_solicitud_retiro"] = None
        inv["porcentaje_retiro"] = None
        inv["monto_retiro_solicitado"] = None
        self._guardar()
        return True

    def listar_retiros_pendientes(self):
        return [{
            "id": inv["id"],
            "name": inv["name"],
            "fecha_solicitud": inv.get("fecha_solicitud_retiro"),
            "porcentaje": inv.get("porcentaje_retiro"),
            "monto_solicitado": inv.get("monto_retiro_solicitado"),
            "capital_actual": inv["capital_actual"],
            "high_water_mark": inv["high_water_mark"],
            "retiro_pendiente": inv["retiro_pendiente"]
        } for inv in self.inversores if inv.get("retiro_pendiente")]

    def generar_resumen(self):
        total = sum(i["capital_actual"] for i in self.inversores)
        return [{
            "id": i["id"],
            "name": i["name"],
            "usuario": i.get("usuario", i["id"]),
            "password": i.get("password", "changeme"),
            "capital_actual": i["capital_actual"],
            "porcentaje": round(i["capital_actual"]/total*100,2) if total else 0,
            "high_water_mark": i["high_water_mark"],
            "ganancia_neta": i["capital_actual"]-i["capital_invertido"],
            "retiro_pendiente": i["retiro_pendiente"],
            "transacciones": i.get("transacciones", [])
        } for i in self.inversores]

    def obtener_historial_global(self):
        txns = []
        for inv in self.inversores:
            for t in inv.get("transacciones", []):
                txns.append({
                    "inversor_id": inv["id"],
                    "inversor_nombre": inv["name"],
                    "fecha": t["fecha"],
                    "tipo": t["tipo"],
                    "monto": t["monto"],
                    "capital_antes": t["capital_antes"],
                    "capital_despues": t["capital_despues"]
                })
        txns.sort(key=lambda x: x["fecha"], reverse=True)
        return txns

    def obtener_rentabilidad_por_periodos(self, inversor_id):
        inv = next((i for i in self.inversores if i["id"] == inversor_id), None)
        if not inv:
            return None

        transacciones = inv.get("transacciones", [])
        rentas = [t for t in transacciones if t.get("tipo") == "rentabilidad"]
        capital_invertido = inv["capital_invertido"]

        hoy = datetime.now().date()
        lunes_semana = hoy - timedelta(days=hoy.weekday())
        inicio_mes = hoy.replace(day=1)

        mes = hoy.month
        if mes in [1, 2, 3]:
            inicio_trimestre = date(hoy.year, 1, 1)
        elif mes in [4, 5, 6]:
            inicio_trimestre = date(hoy.year, 4, 1)
        elif mes in [7, 8, 9]:
            inicio_trimestre = date(hoy.year, 7, 1)
        else:
            inicio_trimestre = date(hoy.year, 10, 1)

        if mes in [1, 2, 3, 4]:
            inicio_cuatrimestre = date(hoy.year, 1, 1)
        elif mes in [5, 6, 7, 8]:
            inicio_cuatrimestre = date(hoy.year, 5, 1)
        else:
            inicio_cuatrimestre = date(hoy.year, 9, 1)

        inicio_año = date(hoy.year, 1, 1)

        def sumar_desde(fecha_inicio):
            return sum(t["monto"] for t in rentas if datetime.strptime(t["fecha"], "%Y-%m-%d %H:%M:%S").date() >= fecha_inicio)

        semana_pnl = sumar_desde(lunes_semana)
        mes_pnl = sumar_desde(inicio_mes)
        trimestre_pnl = sumar_desde(inicio_trimestre)
        cuatrimestre_pnl = sumar_desde(inicio_cuatrimestre)
        año_pnl = sumar_desde(inicio_año)
        total_pnl = sum(t["monto"] for t in rentas)

        def pct(pnl):
            return (pnl / capital_invertido * 100) if capital_invertido != 0 else 0.0

        return {
            "semana":      {"pnl": semana_pnl,      "porcentaje": pct(semana_pnl)},
            "mes":         {"pnl": mes_pnl,         "porcentaje": pct(mes_pnl)},
            "trimestre":   {"pnl": trimestre_pnl,   "porcentaje": pct(trimestre_pnl)},
            "cuatrimestre":{"pnl": cuatrimestre_pnl,"porcentaje": pct(cuatrimestre_pnl)},
            "año":         {"pnl": año_pnl,         "porcentaje": pct(año_pnl)},
            "total":       {"pnl": total_pnl,       "porcentaje": pct(total_pnl)}
        }
