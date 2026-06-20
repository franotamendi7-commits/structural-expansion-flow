class RiskManager:
    def __init__(self, base_risk=0.01, max_risk=0.015, min_risk=0.005):
        self.base_risk = base_risk
        self.max_risk = max_risk
        self.min_risk = min_risk

    def get_risk_pct(self, score, atr, price):
        """
        score : dynamic_score (0-100)
        atr   : ATR del timeframe relevante (ej. 15m o 1h)
        price : precio actual
        Retorna el porcentaje de riesgo a usar (ej. 0.01 = 1%)
        """
        # 1. Factor por score (lineal entre min y max)
        score_factor = (score - 50) / 50.0
        score_risk = self.min_risk + (self.max_risk - self.min_risk) * max(0, score_factor)

        # 2. Factor por volatilidad (ATR relativo)
        vol_pct = atr / price if price > 0 else 0.02
        avg_vol = 0.02   # valor típico para crypto, ajustable
        vol_factor = avg_vol / vol_pct if vol_pct > 0 else 1.0
        vol_factor = max(0.5, min(vol_factor, 1.5))

        final_risk = score_risk * vol_factor
        return max(self.min_risk, min(self.max_risk, final_risk))
