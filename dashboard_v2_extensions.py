"""
Dashboard V2 Extensions - Institutional Engine V2
=================================================

Adds new sections to the existing dashboard:
1. V2 Engine Status
2. Improved Risk Management
3. Backtest Results
4. Alerts Configuration

Usage: Import and call these functions in app.py main()
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import streamlit as st
import pandas as pd
import numpy as np

# ============================================================
# CONSTANTS
# ============================================================

BACKTEST_RESULTS_DIR = Path(__file__).parent / "backtest_results"
AUDIT_RESULTS_DIR = Path(__file__).parent / "audit_results"


# ============================================================
# V2 ENGINE STATUS
# ============================================================

def render_v2_engine_status():
    """Render V2 Engine status section."""
    st.html('<div class="section-title">Institutional Engine V2</div>')
    
    # Load V2 results if available
    v2_results = _load_v2_results()
    
    if v2_results:
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "V2 Status",
                "Active" if v2_results.get("active", False) else "Inactive",
                delta=None,
            )
        
        with col2:
            sharpe = v2_results.get("sharpe", 0)
            st.metric(
                "Sharpe Ratio",
                f"{sharpe:.2f}",
                delta=f"{sharpe - 1.0:.2f}" if sharpe > 0 else None,
                delta_color="normal" if sharpe >= 1.0 else "inverse",
            )
        
        with col3:
            dd = v2_results.get("max_dd_pct", 0)
            st.metric(
                "Max Drawdown",
                f"{dd:.2f}%",
                delta=None,
                delta_color="inverse",
            )
        
        with col4:
            pf = v2_results.get("profit_factor", 0)
            st.metric(
                "Profit Factor",
                f"{pf:.2f}",
                delta=f"{pf - 1.0:.2f}" if pf > 0 else None,
            )
        
        # Detailed metrics
        with st.expander("V2 Detailed Metrics", expanded=False):
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Performance:**")
                st.write(f"- Total Return: {v2_results.get('total_return_pct', 0):+.2f}%")
                st.write(f"- Win Rate: {v2_results.get('win_rate', 0):.1f}%")
                st.write(f"- Trades: {v2_results.get('total_trades', 0)}")
                st.write(f"- Avg Trade: ${v2_results.get('avg_trade', 0):.4f}")
            
            with col2:
                st.write("**Risk:**")
                st.write(f"- Sortino: {v2_results.get('sortino', 0):.2f}")
                st.write(f"- Calmar: {v2_results.get('calmar', 0):.2f}")
                st.write(f"- Avg Win: ${v2_results.get('avg_win', 0):.4f}")
                st.write(f"- Avg Loss: ${v2_results.get('avg_loss', 0):.4f}")
        
        # Drawdown Manager Status
        _render_drawdown_manager_status()
        
        # Parameter Adapter Status
        _render_parameter_adapter_status()
    else:
        st.info("V2 Engine results not available. Run backtest first.")


def _load_v2_results() -> Optional[Dict]:
    """Load V2 backtest results."""
    try:
        # Try to load from tp_adjustment_result.json
        tp_file = BACKTEST_RESULTS_DIR / "tp_adjustment_result.json"
        if tp_file.exists():
            with open(tp_file) as f:
                data = json.load(f)
                return data.get("metrics", {})
        
        # Try to load from any v2 backtest result
        for file in BACKTEST_RESULTS_DIR.glob("*v2*.json"):
            with open(file) as f:
                data = json.load(f)
                if "v2" in data:
                    return data["v2"]
    except Exception:
        pass
    return None


def _render_drawdown_manager_status():
    """Render Drawdown Manager 5-level visual."""
    st.write("**Drawdown Manager (5 Levels):**")
    
    levels = [
        ("Level 1", "0-3%", "100% risk", "#4CAF50"),
        ("Level 2", "3-5%", "75% risk", "#FFC107"),
        ("Level 3", "5-7%", "50% risk", "#FF9800"),
        ("Level 4", "7-9%", "25% risk", "#F44336"),
        ("Level 5", ">9%", "STOP", "#9C27B0"),
    ]
    
    cols = st.columns(5)
    for i, (name, dd_range, risk_mult, color) in enumerate(levels):
        with cols[i]:
            st.html(f"""
            <div style="
                background: {color}20;
                border: 2px solid {color};
                border-radius: 8px;
                padding: 12px;
                text-align: center;
            ">
                <div style="font-weight: 600; color: {color};">{name}</div>
                <div style="font-size: 0.8rem; color: var(--text-muted);">{dd_range}</div>
                <div style="font-weight: 500; margin-top: 4px;">{risk_mult}</div>
            </div>
            """)


def _render_parameter_adapter_status():
    """Render Parameter Adapter status."""
    st.write("**Parameter Adapter:**")
    
    try:
        param_file = Path(__file__).parent / "parameter_state.json"
        if param_file.exists():
            with open(param_file) as f:
                state = json.load(f)
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Trades Since Adapt", state.get("trades_since_adapt", 0))
            with col2:
                st.metric("Total Trades", state.get("total_trades", 0))
            with col3:
                st.metric("Adaptations", state.get("adaptation_count", 0))
        else:
            st.info("Parameter adapter state not found")
    except Exception:
        st.info("Parameter adapter state unavailable")


# ============================================================
# IMPROVED RISK MANAGEMENT
# ============================================================

def render_improved_risk():
    """Render improved risk management section."""
    st.html('<div class="section-title">Risk Management (Enhanced)</div>')
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        # Equity Curve with Mark-to-Market
        _render_equity_curve()
        
        # Exposure by Pair
        _render_exposure_by_pair()
    
    with col2:
        # Correlation Matrix
        _render_correlation_matrix()
        
        # Monte Carlo Distribution
        _render_monte_carlo_distribution()


def _render_equity_curve():
    """Render equity curve with mark-to-market."""
    st.write("**Equity Curve:**")
    
    equity_history = st.session_state.get("equity_history", [])
    if equity_history:
        df = pd.DataFrame(equity_history)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df["timestamp"],
                y=df["equity"],
                mode="lines",
                name="Equity",
                line=dict(color="#4CAF50", width=2),
            ))
            fig.update_layout(
                height=300,
                margin=dict(l=0, r=0, t=0, b=0),
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No equity history with timestamps")
    else:
        st.info("No equity history available")


def _render_exposure_by_pair():
    """Render exposure by trading pair."""
    st.write("**Exposure by Pair:**")
    
    system = st.session_state.get("system")
    if system is None:
        st.info("System not available")
        return
    
    exposures = {}
    for name, bot in system.bots.items():
        if hasattr(bot, "position") and bot.position:
            exposure = bot.position.get("size", 0) * bot.position.get("entry_price", 0)
            exposures[name] = abs(exposure)
    
    if exposures:
        df = pd.DataFrame([
            {"Pair": k, "Exposure": v}
            for k, v in exposures.items()
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions")


def _render_correlation_matrix():
    """Render correlation matrix of recent returns."""
    st.write("**Correlation Matrix:**")
    
    # Placeholder - would need historical price data
    st.info("Correlation matrix requires historical price data")


def _render_monte_carlo_distribution():
    """Render Monte Carlo distribution chart."""
    st.write("**Monte Carlo Distribution:**")
    
    # Try to load Monte Carlo results
    mc_file = BACKTEST_RESULTS_DIR / "monte_carlo_exhaustive_v2.json"
    if mc_file.exists():
        with open(mc_file) as f:
            data = json.load(f)
        
        scenarios = data.get("scenarios", {})
        if "base" in scenarios:
            results = scenarios["base"]["results"]
            
            # Create distribution visualization
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Mean Return", f"{results.get('mean_return', 0):+.2f}%")
                st.metric("Prob Profit", f"{results.get('prob_profit', 0):.1%}")
            with col2:
                st.metric("Mean DD", f"{results.get('max_dd', {}).get('mean', 0):.2f}%")
                st.metric("Sharpe", f"{results.get('sharpe', {}).get('mean', 0):.2f}")
            
            # Percentiles
            percentiles = results.get("percentiles", {})
            if percentiles:
                st.write("**Return Percentiles:**")
                cols = st.columns(5)
                for i, (key, label) in enumerate([
                    ("p5", "5th"), ("p25", "25th"), ("p50", "50th"),
                    ("p75", "75th"), ("p95", "95th")
                ]):
                    with cols[i]:
                        st.metric(label, f"{percentiles.get(key, 0):+.2f}%")
    else:
        st.info("Monte Carlo results not available")


# ============================================================
# BACKTEST RESULTS
# ============================================================

def render_backtest_results():
    """Render backtest results section."""
    st.html('<div class="section-title">Backtest Results</div>')
    
    tab1, tab2, tab3, tab4 = st.tabs([
        "Walk-Forward", "Multi-Asset", "Sensitivity", "Monte Carlo"
    ])
    
    with tab1:
        _render_walkforward_results()
    
    with tab2:
        _render_multiasset_results()
    
    with tab3:
        _render_sensitivity_results()
    
    with tab4:
        _render_monte_carlo_results()


def _render_walkforward_results():
    """Render walk-forward results by fold."""
    st.write("**Walk-Forward Analysis (7 Folds):**")
    
    # Try to load walkforward results
    wf_files = list(BACKTEST_RESULTS_DIR.parent.glob("walkforward*results*.json"))
    if wf_files:
        with open(wf_files[0]) as f:
            data = json.load(f)
        
        if isinstance(data, dict) and "folds" in data:
            folds = data["folds"]
            df = pd.DataFrame(folds)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("Walk-forward data format not recognized")
    else:
        st.info("Walk-forward results not available")


def _render_multiasset_results():
    """Render multi-asset comparison."""
    st.write("**Multi-Asset Results (5 Pairs):**")
    
    # Try to load multiasset results
    ma_file = BACKTEST_RESULTS_DIR.parent / "backtest_multiasset_results.json"
    if ma_file.exists():
        with open(ma_file) as f:
            data = json.load(f)
        
        if isinstance(data, dict):
            df = pd.DataFrame([
                {"Pair": k, **v}
                for k, v in data.items()
                if isinstance(v, dict)
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Multi-asset results not available")


def _render_sensitivity_results():
    """Render sensitivity analysis charts."""
    st.write("**Sensitivity Analysis:**")
    
    # Try to load sensitivity results
    sens_file = BACKTEST_RESULTS_DIR.parent / "backtest_sensitivity_results.json"
    if sens_file.exists():
        with open(sens_file) as f:
            data = json.load(f)
        
        st.json(data)
    else:
        st.info("Sensitivity analysis results not available")


def _render_monte_carlo_results():
    """Render Monte Carlo simulation results."""
    st.write("**Monte Carlo Simulation (10,000 sims):**")
    
    mc_file = BACKTEST_RESULTS_DIR / "monte_carlo_exhaustive_v2.json"
    if mc_file.exists():
        with open(mc_file) as f:
            data = json.load(f)
        
        scenarios = data.get("scenarios", {})
        
        # Summary table
        rows = []
        for name, scenario in scenarios.items():
            results = scenario.get("results", {})
            rows.append({
                "Scenario": name,
                "Mean Return": f"{results.get('mean_return', 0):+.2f}%",
                "Prob Profit": f"{results.get('prob_profit', 0):.1%}",
                "Mean DD": f"{results.get('max_dd', {}).get('mean', 0):.2f}%",
                "Sharpe": f"{results.get('sharpe', {}).get('mean', 0):.2f}",
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Success criteria
        st.write("**Success Criteria:**")
        criteria = data.get("criteria", {})
        for criterion_name, criterion_data in criteria.items():
            for scenario_name, check in criterion_data.items():
                status = "PASS" if check.get("pass", False) else "FAIL"
                st.write(f"- {criterion_name} ({scenario_name}): {status}")
    else:
        st.info("Monte Carlo results not available")


# ============================================================
# ALERTS CONFIGURATION
# ============================================================

def render_alerts_config():
    """Render alerts configuration section."""
    st.html('<div class="section-title">Alerts Configuration</div>')
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Telegram Alerts:**")
        
        # Check if Telegram is configured
        telegram_configured = False
        try:
            secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
            if secrets_path.exists():
                telegram_configured = True
        except Exception:
            pass
        
        if telegram_configured:
            st.success("Telegram configured")
        else:
            st.warning("Telegram not configured")
        
        st.write("**Alert Types:**")
        st.write("- Trade entries/exits")
        st.write("- DD > 5% warning")
        st.write("- DD > 10% critical")
        st.write("- Parameter changes")
        st.write("- Daily summary")
    
    with col2:
        st.write("**Alert Thresholds:**")
        
        # Load current thresholds
        try:
            risk_state_file = Path(__file__).parent / "risk_state.json"
            if risk_state_file.exists():
                with open(risk_state_file) as f:
                    risk_state = json.load(f)
                
                st.write(f"- DD Warning: {risk_state.get('dd_warning', 5)}%")
                st.write(f"- DD Critical: {risk_state.get('dd_critical', 10)}%")
                st.write(f"- DD Stop: {risk_state.get('dd_stop', 9)}%")
            else:
                st.info("Risk state not available")
        except Exception:
            st.info("Risk state unavailable")


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def render_v2_dashboard_extensions():
    """Main function to render all V2 extensions."""
    render_v2_engine_status()
    st.markdown("---")
    render_improved_risk()
    st.markdown("---")
    render_backtest_results()
    st.markdown("---")
    render_alerts_config()
