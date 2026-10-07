#!/usr/bin/env python3
"""Fail on malformed or implausible generated ERCOT data."""

import json
from pathlib import Path

import pandas as pd

DATA = Path("data")


def main() -> None:
    plants = pd.read_csv(DATA / "ercot_gas_plants.csv")
    generators = pd.read_csv(DATA / "ercot_gas_generators.csv")
    demand = pd.read_csv(DATA / "ercot_gas_demand_monthly.csv")
    summary = json.loads((DATA / "summary.json").read_text())
    assert plants["eia_plant_id_orispl"].is_unique, "ORISPL must be unique in plant table"
    assert plants["plant_state"].isin({"TX", "OK"}).all(), "unexpected plant state for ERCOT records"
    assert plants[["latitude", "longitude"]].notna().all().all(), "plant coordinates are missing"
    assert (~plants.loc[plants["plant_state"].eq("OK"), "ercot_footprint"]).all(), "OK plant must be outside geographic footprint"
    assert generators[["orispl", "gen_id", "prime_mover", "technology"]].notna().all().all(), "generator identifiers missing"
    assert generators["orispl"].nunique() == len(plants), "generator-to-plant coverage mismatch"
    assert plants["gas_summer_capacity_mw"].ge(0).all(), "negative capacity"
    assert demand[["gas_consumed_mcf", "gas_consumed_mmbtu"]].fillna(0).ge(0).all().all(), "negative gas demand"
    assert demand.loc[demand["net_generation_mwh"] > 0, "heat_rate_mmbtu_per_mwh"].notna().all(), "heat rate missing"
    capacity_basis = demand["capacity_factor_basis_summer_mw"].fillna(0) > 0
    assert demand.loc[capacity_basis, "capacity_factor"].notna().all(), "capacity factor missing where capacity basis exists"
    for frame in [plants, generators, demand]:
        assert frame[["source_file", "source_url", "retrieved_at"]].notna().all().all(), "row provenance missing"
    assert len(plants) >= 150, "unexpectedly small plant inventory"
    assert plants["gas_summer_capacity_mw"].sum() >= 40_000, "unexpectedly low ERCOT gas capacity"
    assert summary["capacity_inventory_plant_count"] == len(plants), "summary plant count mismatch"
    assert abs(summary["gas_summer_capacity_mw"] - plants["gas_summer_capacity_mw"].sum()) < 0.01
    assert summary["capacity_site_demand_coverage_pct"] >= 90, "plant demand coverage below 90%"
    assert summary["capacity_mw_demand_coverage_pct"] >= 95, "capacity-weighted demand coverage below 95%"
    print(f"Validated {len(plants)} plants, {len(generators)} generators, and {len(demand)} plant-month records")


if __name__ == "__main__":
    main()
