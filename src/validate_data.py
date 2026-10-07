#!/usr/bin/env python3
"""Fail on malformed or implausible generated ERCOT data."""

import json
from pathlib import Path

import pandas as pd

DATA = Path("data")


def main() -> None:
    plants = pd.read_csv(DATA / "ercot_gas_plants.csv")
    demand = pd.read_csv(DATA / "ercot_gas_demand_monthly.csv")
    summary = json.loads((DATA / "summary.json").read_text())
    assert plants["plant_id"].is_unique, "plant_id must be unique in plant table"
    assert plants["plant_state"].isin({"TX", "OK"}).all(), "unexpected plant state for ERCOT records"
    assert plants[["latitude", "longitude"]].notna().all().all(), "plant coordinates are missing"
    assert plants["gas_summer_capacity_mw"].ge(0).all(), "negative capacity"
    assert demand[["gas_consumed_mcf", "gas_consumed_mmbtu"]].fillna(0).ge(0).all().all(), "negative gas demand"
    assert len(plants) >= 150, "unexpectedly small plant inventory"
    assert plants["gas_summer_capacity_mw"].sum() >= 40_000, "unexpectedly low ERCOT gas capacity"
    assert summary["plant_count"] == len(plants), "summary plant count mismatch"
    assert abs(summary["gas_summer_capacity_mw"] - plants["gas_summer_capacity_mw"].sum()) < 0.01
    print(f"Validated {len(plants)} plants and {len(demand)} plant-month records")


if __name__ == "__main__":
    main()
