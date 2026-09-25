import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_small_live_config_is_deliberately_bounded():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert config["execution_enabled"] is True
    assert config["max_new_legs_per_scan"] == 1
    assert config["requested_leverage"] == 20
    assert config["max_loss_per_leg_equity_fraction"] == 0.03


def test_shadow_service_does_not_construct_private_executor():
    source = (ROOT / "v5_main.py").read_text(encoding="utf-8")
    assert "V4ExecutionManager(" not in source
    assert "if execution_enabled:" in source
    assert "executor = V5ExecutionManager(" in source
    assert "scan_loop(config, scanner, universes, executor=executor)" in source


def test_five_asset_factor_budgets_are_explicit():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    limits = config["risk_factor_margin_limits"]
    assert set(("EQUITY", "CRYPTO", "ENERGY", "METALS", "RATES")) <= set(limits)
    assert all(0 < limits[key] <= 0.45 for key in limits)
    assert config["max_factor_side_risk_share"] == 0.70


def test_preexisting_position_requires_v5_ledger_ownership_before_exit():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert config["preexisting_position_policy"] == (
        "count_risk_do_not_manage_without_ledger")
    source = (ROOT / "app" / "services" / "strategy_engine.py").read_text(
        encoding="utf-8")
    ownership = source.index("if not await self._strategy_owns_ledger_position")
    first_reduce_after_ownership = source.index("await self._do_reduce", ownership)
    assert ownership < first_reduce_after_ownership
    execution_source = (ROOT / "v5_execution.py").read_text(encoding="utf-8")
    assert "_run_reconcile_guarded" not in execution_source
    assert config["ignored_preexisting_symbols"] == ["SOXL-USDT-SWAP"]
