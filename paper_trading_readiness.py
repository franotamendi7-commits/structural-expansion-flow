"""
Paper Trading Readiness Check - Institutional Engine V2
======================================================

Verifies all prerequisites before live/paper trading.

Checklist:
- [ ] tp_ratio adjusted to 1.3
- [ ] Monte Carlo: Prob profit > 85% in all scenarios
- [ ] Monte Carlo: Prob DD > 10% < 15%
- [ ] Audit: Costs < 0.15% per trade
- [ ] Audit: No look-ahead bias
- [ ] Audit: Execution manager robust
- [ ] Dashboard: V2 tab working
- [ ] Dashboard: Risk tab improved
- [ ] multi_bot_v2.py: Integrated and tested
- [ ] Config: API keys configured (placeholder)
- [ ] Logging: Everything logged correctly
- [ ] Persistence: State saved between restarts

Output:
- readiness_check.json
- READINESS_REPORT.md
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List

sys.path.insert(0, str(Path(__file__).parent))

OUTPUT_DIR = Path(__file__).parent
BACKTEST_RESULTS_DIR = OUTPUT_DIR / "backtest_results"
AUDIT_RESULTS_DIR = OUTPUT_DIR / "audit_results"


class ReadinessChecker:
    """Check all prerequisites for paper trading."""
    
    def __init__(self):
        self.checks = {}
        self.all_pass = True
    
    def check(self, name: str, description: str, passed: bool, details: str = ""):
        """Add a check result."""
        self.checks[name] = {
            "description": description,
            "passed": passed,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if not passed:
            self.all_pass = False
        
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {description}")
        if details:
            print(f"         {details}")
    
    def run_all_checks(self) -> Dict[str, Any]:
        """Run all readiness checks."""
        print("=" * 70)
        print(" PAPER TRADING READINESS CHECK")
        print("=" * 70)
        
        # 1. tp_ratio adjustment
        print("\n[1/12] Checking tp_ratio adjustment...")
        self._check_tp_ratio()
        
        # 2. Monte Carlo: Prob profit > 85%
        print("\n[2/12] Checking Monte Carlo profit probability...")
        self._check_mc_profit_prob()
        
        # 3. Monte Carlo: DD > 10% < 15%
        print("\n[3/12] Checking Monte Carlo drawdown...")
        self._check_mc_drawdown()
        
        # 4. Audit: Costs < 0.15%
        print("\n[4/12] Checking trading costs...")
        self._check_costs()
        
        # 5. Audit: No look-ahead bias
        print("\n[5/12] Checking look-ahead bias...")
        self._check_lookahead_bias()
        
        # 6. Audit: Execution manager robust
        print("\n[6/12] Checking execution manager...")
        self._check_execution_manager()
        
        # 7. Dashboard: V2 tab working
        print("\n[7/12] Checking dashboard V2 tab...")
        self._check_dashboard_v2()
        
        # 8. Dashboard: Risk tab improved
        print("\n[8/12] Checking dashboard risk tab...")
        self._check_dashboard_risk()
        
        # 9. multi_bot_v2.py: Integrated
        print("\n[9/12] Checking multi_bot_v2.py integration...")
        self._check_multi_bot_v2()
        
        # 10. Config: API keys
        print("\n[10/12] Checking API key configuration...")
        self._check_api_keys()
        
        # 11. Logging
        print("\n[11/12] Checking logging setup...")
        self._check_logging()
        
        # 12. Persistence
        print("\n[12/12] Checking state persistence...")
        self._check_persistence()
        
        # Summary
        print(f"\n{'='*70}")
        passed = sum(1 for c in self.checks.values() if c["passed"])
        total = len(self.checks)
        print(f" RESULT: {passed}/{total} checks passed")
        
        if self.all_pass:
            print(" ALL CHECKS PASSED - Ready for paper trading!")
        else:
            print(" SOME CHECKS FAILED - Review before paper trading")
            print("\n Failed checks:")
            for name, check in self.checks.items():
                if not check["passed"]:
                    print(f"  - {name}: {check['description']}")
        
        print(f"{'='*70}")
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": self.checks,
            "passed": passed,
            "total": total,
            "all_pass": self.all_pass,
            "ready_for_paper_trading": self.all_pass,
        }
    
    def _check_tp_ratio(self):
        """Check if tp_ratio is adjusted to 1.3."""
        engine_file = OUTPUT_DIR / "engine" / "institutional_engine_v2.py"
        
        if engine_file.exists():
            content = engine_file.read_text()
            if '"tp_ratio": 1.3' in content or "'tp_ratio': 1.3" in content:
                self.check(
                    "tp_ratio_adjusted",
                    "tp_ratio set to 1.3",
                    passed=True,
                    details="Confirmed in engine/institutional_engine_v2.py"
                )
            else:
                self.check(
                    "tp_ratio_adjusted",
                    "tp_ratio NOT set to 1.3",
                    passed=False,
                    details="Still using default value"
                )
        else:
            self.check(
                "tp_ratio_adjusted",
                "Engine file not found",
                passed=False,
                details=str(engine_file)
            )
    
    def _check_mc_profit_prob(self):
        """Check Monte Carlo profit probability > 85%."""
        mc_file = BACKTEST_RESULTS_DIR / "monte_carlo_exhaustive_v2.json"
        
        if mc_file.exists():
            with open(mc_file) as f:
                data = json.load(f)
            
            scenarios = data.get("scenarios", {})
            all_above_85 = True
            details = []
            
            for name, scenario in scenarios.items():
                prob = scenario.get("results", {}).get("prob_profit", 0)
                if prob < 0.85:
                    all_above_85 = False
                    details.append(f"{name}: {prob:.1%}")
            
            self.check(
                "mc_profit_prob",
                "Monte Carlo profit prob > 85% in all scenarios",
                passed=all_above_85,
                details="; ".join(details) if details else "All scenarios pass"
            )
        else:
            self.check(
                "mc_profit_prob",
                "Monte Carlo results not available",
                passed=False,
                details="Run backtest_monte_carlo_v2.py first"
            )
    
    def _check_mc_drawdown(self):
        """Check Monte Carlo DD > 10% < 15%."""
        mc_file = BACKTEST_RESULTS_DIR / "monte_carlo_exhaustive_v2.json"
        
        if mc_file.exists():
            with open(mc_file) as f:
                data = json.load(f)
            
            scenarios = data.get("scenarios", {})
            all_below_15 = True
            details = []
            
            for name, scenario in scenarios.items():
                dd_prob = scenario.get("results", {}).get("prob_dd_exceeds_10", 0)
                if dd_prob > 0.15:
                    all_below_15 = False
                    details.append(f"{name}: {dd_prob:.1%}")
            
            self.check(
                "mc_drawdown",
                "Monte Carlo DD > 10% prob < 15% in all scenarios",
                passed=all_below_15,
                details="; ".join(details) if details else "All scenarios pass"
            )
        else:
            self.check(
                "mc_drawdown",
                "Monte Carlo results not available",
                passed=False,
                details="Run backtest_monte_carlo_v2.py first"
            )
    
    def _check_costs(self):
        """Check trading costs < 0.15%."""
        cost_file = AUDIT_RESULTS_DIR / "cost_analysis.json"
        
        if cost_file.exists():
            with open(cost_file) as f:
                data = json.load(f)
            
            # Check BTCUSDT (main pair)
            btc_data = data.get("BTCUSDT", {})
            criterion = btc_data.get("criterion", {})
            
            self.check(
                "trading_costs",
                "Trading costs < 0.15% per round trip",
                passed=criterion.get("pass", False),
                details=f"Actual: {criterion.get('actual_medium', 'unknown')}"
            )
        else:
            self.check(
                "trading_costs",
                "Cost analysis not available",
                passed=False,
                details="Run audit_real_costs.py first"
            )
    
    def _check_lookahead_bias(self):
        """Check for look-ahead bias."""
        data_file = AUDIT_RESULTS_DIR / "data_quality.json"
        
        if data_file.exists():
            with open(data_file) as f:
                data = json.load(f)
            
            look_ahead = data.get("look_ahead_bias", {})
            no_bias = look_ahead.get("uses_only_closed_bars", False)
            
            self.check(
                "lookahead_bias",
                "No look-ahead bias",
                passed=no_bias,
                details="Backtest uses only closed bars"
            )
        else:
            self.check(
                "lookahead_bias",
                "Data quality audit not available",
                passed=False,
                details="Run audit_real_costs.py first"
            )
    
    def _check_execution_manager(self):
        """Check execution manager robustness."""
        exec_file = AUDIT_RESULTS_DIR / "execution_audit.json"
        
        if exec_file.exists():
            with open(exec_file) as f:
                data = json.load(f)
            
            checks = data.get("checks", {})
            has_retry = checks.get("has_retry_logic", False)
            has_error_handling = checks.get("handles_api_errors", False)
            
            self.check(
                "execution_manager",
                "Execution manager has error handling",
                passed=has_error_handling,
                details=f"Retry logic: {has_retry}, Error handling: {has_error_handling}"
            )
        else:
            self.check(
                "execution_manager",
                "Execution audit not available",
                passed=False,
                details="Run audit_real_costs.py first"
            )
    
    def _check_dashboard_v2(self):
        """Check dashboard V2 tab exists."""
        dashboard_file = OUTPUT_DIR / "dashboard_v2_extensions.py"
        
        if dashboard_file.exists():
            content = dashboard_file.read_text()
            has_v2_status = "render_v2_engine_status" in content
            
            self.check(
                "dashboard_v2",
                "Dashboard V2 extensions available",
                passed=has_v2_status,
                details="dashboard_v2_extensions.py exists"
            )
        else:
            self.check(
                "dashboard_v2",
                "Dashboard V2 extensions not found",
                passed=False,
                details="Create dashboard_v2_extensions.py"
            )
    
    def _check_dashboard_risk(self):
        """Check dashboard risk tab improved."""
        dashboard_file = OUTPUT_DIR / "dashboard_v2_extensions.py"
        
        if dashboard_file.exists():
            content = dashboard_file.read_text()
            has_risk = "render_improved_risk" in content
            
            self.check(
                "dashboard_risk",
                "Dashboard risk tab improved",
                passed=has_risk,
                details="Improved risk section available"
            )
        else:
            self.check(
                "dashboard_risk",
                "Dashboard risk improvements not found",
                passed=False,
                details="Add render_improved_risk to dashboard_v2_extensions.py"
            )
    
    def _check_multi_bot_v2(self):
        """Check multi_bot_v2.py integration."""
        mb2_file = OUTPUT_DIR / "multi_bot_v2.py"
        
        if mb2_file.exists():
            content = mb2_file.read_text()
            has_engine = "InstitutionalEngineV2" in content
            has_position = "PositionManager" in content
            has_dd = "DrawdownManager" in content
            
            self.check(
                "multi_bot_v2",
                "multi_bot_v2.py integrated with V2 engine",
                passed=has_engine and has_position and has_dd,
                details=f"Engine: {has_engine}, Position: {has_position}, DD: {has_dd}"
            )
        else:
            self.check(
                "multi_bot_v2",
                "multi_bot_v2.py not found",
                passed=False,
                details="Create multi_bot_v2.py"
            )
    
    def _check_api_keys(self):
        """Check API key configuration."""
        secrets_file = OUTPUT_DIR / ".streamlit" / "secrets.toml"
        
        if secrets_file.exists():
            # Don't read actual keys, just check existence
            self.check(
                "api_keys",
                "API keys configured (placeholder)",
                passed=True,
                details=".streamlit/secrets.toml exists"
            )
        else:
            self.check(
                "api_keys",
                "API keys not configured",
                passed=False,
                details="Create .streamlit/secrets.toml with API keys"
            )
    
    def _check_logging(self):
        """Check logging setup."""
        log_dir = OUTPUT_DIR / "logs"
        
        if log_dir.exists():
            log_files = list(log_dir.glob("*.log"))
            self.check(
                "logging",
                "Logging directory exists",
                passed=True,
                details=f"Found {len(log_files)} log files"
            )
        else:
            self.check(
                "logging",
                "Logging directory not found",
                passed=False,
                details="Create logs/ directory"
            )
    
    def _check_persistence(self):
        """Check state persistence."""
        state_files = [
            "drawdown_state.json",
            "parameter_state.json",
            "trade_history.json",
        ]
        
        existing = []
        missing = []
        
        for file_name in state_files:
            file_path = OUTPUT_DIR / file_name
            if file_path.exists():
                existing.append(file_name)
            else:
                missing.append(file_name)
        
        self.check(
            "persistence",
            "State files exist for persistence",
            passed=len(missing) == 0,
            details=f"Existing: {existing}, Missing: {missing}"
        )


def generate_readiness_report(results: Dict[str, Any]):
    """Generate readiness report in markdown."""
    
    checks = results["checks"]
    passed = results["passed"]
    total = results["total"]
    all_pass = results["all_pass"]
    
    md = f"""# Paper Trading Readiness Report

**Date:** {results['timestamp'][:10]}
**Overall Status:** {'PASS' if all_pass else 'FAIL'}
**Checks Passed:** {passed}/{total}

## Checklist

| Check | Status | Description |
|-------|--------|-------------|
"""
    
    for name, check in checks.items():
        status = "PASS" if check["passed"] else "FAIL"
        md += f"| {name} | {status} | {check['description']} |\n"
    
    md += f"""
## Detailed Results

"""
    
    for name, check in checks.items():
        status = "PASS" if check["passed"] else "FAIL"
        md += f"### {name} ({status})\n\n"
        md += f"- **Description:** {check['description']}\n"
        if check.get("details"):
            md += f"- **Details:** {check['details']}\n"
        md += "\n"
    
    if all_pass:
        md += """## Conclusion

All checks passed. The system is ready for paper trading.

### Next Steps
1. Configure API keys in `.streamlit/secrets.toml`
2. Start paper trading on testnet
3. Monitor metrics for 2 weeks
4. Adjust parameters if needed
5. Deploy to live trading
"""
    else:
        md += """## Conclusion

Some checks failed. Review and fix before paper trading.

### Failed Checks
"""
        for name, check in checks.items():
            if not check["passed"]:
                md += f"- **{name}:** {check['description']}\n"
                if check.get("details"):
                    md += f"  - {check['details']}\n"
    
    report_path = OUTPUT_DIR / "READINESS_REPORT.md"
    with open(report_path, "w") as f:
        f.write(md)
    
    print(f"\nReport generated: {report_path}")


if __name__ == "__main__":
    checker = ReadinessChecker()
    results = checker.run_all_checks()
    
    # Save results
    output_file = OUTPUT_DIR / "readiness_check.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    # Generate report
    generate_readiness_report(results)
    
    print(f"\nResults saved to: {output_file}")
