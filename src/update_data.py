#!/usr/bin/env python3
"""Refresh ERCOT natural-gas plant capacity and monthly fuel demand from EIA."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import pandas as pd

EIA_860M_PAGE = "https://www.eia.gov/electricity/data/eia860m/"
EIA_923_PAGE = "https://www.eia.gov/electricity/data/eia923/"
ERCOT_CAPACITY_PAGE = "https://www.ercot.com/gridinfo/resource"
GAS_FUEL_CODES = {"NG"}
ERCOT_BA_CODE = "ERCO"
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def get(url: str, *, timeout: int = 120) -> bytes:
    request = Request(url, headers={"User-Agent": "ercot-gas-plants/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def matching_links(page_url: str, pattern: str) -> list[str]:
    html = get(page_url).decode("utf-8", errors="replace")
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    links = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    matches = [urljoin(page_url, link) for link in links if re.search(pattern, link, flags=re.I)]
    if not matches:
        raise RuntimeError(f"No source link matching {pattern!r} found on {page_url}")

    def date_key(url: str) -> tuple[int, int]:
        year = max([int(x) for x in re.findall(r"20\d{2}", url)] or [0])
        month_names = {name.lower(): i for i, name in enumerate(MONTHS, 1)}
        name_month = max([month_names.get(x.lower(), 0) for x in re.findall(r"[A-Za-z]+", url)] or [0])
        numeric_months = [int(x) for x in re.findall(r"(?:_|/)(0?[1-9]|1[0-2])(?:_|/)", url)]
        return year, max([name_month, *numeric_months])

    return sorted(set(matches), key=date_key, reverse=True)


def latest_available_download(page_url: str, pattern: str, destination: Path) -> dict:
    errors = []
    for url in matching_links(page_url, pattern):
        try:
            payload = get(url, timeout=30)
            if not payload.startswith(b"PK"):
                errors.append(f"{url}: response is not an Excel/ZIP file")
                continue
            destination.write_bytes(payload)
            return {
                "url": url,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("No downloadable source file found:\n" + "\n".join(errors[:12]))


def latest_available_downloads(page_url: str, pattern: str, destinations: list[Path]) -> list[dict]:
    results = []
    errors = []
    for url in matching_links(page_url, pattern):
        if len(results) >= len(destinations):
            break
        try:
            payload = get(url, timeout=30)
            if not payload.startswith(b"PK"):
                errors.append(f"{url}: response is not an Excel/ZIP file")
                continue
            destination = destinations[len(results)]
            destination.write_bytes(payload)
            results.append({
                "url": url,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            })
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if len(results) != len(destinations):
        raise RuntimeError("Not enough downloadable source files found:\n" + "\n".join(errors[:12]))
    return results


def download(url: str, destination: Path) -> dict:
    payload = get(url)
    destination.write_bytes(payload)
    return {
        "url": url,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def file_metadata(path: Path, url: str | None = None) -> dict:
    payload = path.read_bytes()
    result = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    if url:
        result["url"] = url
    return result


def clean_number(series: pd.Series) -> pd.Series:
    cleaned = series.mask(series.astype(str).str.strip().isin({".", ""}), pd.NA)
    return pd.to_numeric(cleaned, errors="coerce")


def read_capacity(path: Path) -> tuple[pd.DataFrame, str]:
    raw = pd.read_excel(path, sheet_name="Operating", header=2, dtype={"Generator ID": str})
    title = pd.read_excel(path, sheet_name="Operating", header=None, nrows=1).iloc[0, 0]
    rows = raw[
        (raw["Balancing Authority Code"].astype(str).str.strip() == ERCOT_BA_CODE)
        & (raw["Energy Source Code"].astype(str).str.strip().isin(GAS_FUEL_CODES))
    ].copy()
    for col in ["Nameplate Capacity (MW)", "Net Summer Capacity (MW)", "Net Winter Capacity (MW)"]:
        rows[col] = clean_number(rows[col])
    rows["Plant ID"] = pd.to_numeric(rows["Plant ID"], errors="raise").astype(int)

    plants = (
        rows.groupby(["Plant ID", "Plant Name", "County", "Plant State", "Latitude", "Longitude"], dropna=False)
        .agg(
            gas_unit_count=("Generator ID", "nunique"),
            gas_nameplate_capacity_mw=("Nameplate Capacity (MW)", "sum"),
            gas_summer_capacity_mw=("Net Summer Capacity (MW)", "sum"),
            gas_winter_capacity_mw=("Net Winter Capacity (MW)", "sum"),
            technologies=("Technology", lambda x: " | ".join(sorted(set(map(str, x.dropna()))))),
            operator=("Entity Name", "first"),
        )
        .reset_index()
    )
    plants.columns = [re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_") for c in plants.columns]
    plants = plants.rename(columns={"plant_id": "eia_plant_id_orispl"})
    return plants, str(title)


def read_demand(path: Path) -> tuple[pd.DataFrame, str]:
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as directory:
        names = [n for n in archive.namelist() if n.lower().endswith(".xlsx")]
        if not names:
            raise RuntimeError("EIA-923 archive contains no Excel workbook")
        archive.extract(names[0], directory)
        workbook = Path(directory) / names[0]
        title = pd.read_excel(workbook, sheet_name="Page 1 Generation and Fuel Data", header=None, nrows=2).iloc[1, 0]
        raw = pd.read_excel(workbook, sheet_name="Page 1 Generation and Fuel Data", header=5)

    raw.columns = [str(c).replace("\n", " ").strip() for c in raw.columns]
    rows = raw[
        (raw["Balancing Authority Code"].astype(str).str.strip() == ERCOT_BA_CODE)
        & (raw["Reported Fuel Type Code"].astype(str).str.strip().isin(GAS_FUEL_CODES))
    ].copy()
    rows["Plant Id"] = pd.to_numeric(rows["Plant Id"], errors="raise").astype(int)
    year = int(pd.to_numeric(rows["YEAR"], errors="coerce").dropna().max())
    records = []
    for month_number, month in enumerate(MONTHS, 1):
        mcf_col = f"Elec_Quantity {month}"
        mmbtu_col = f"Elec_MMBtu {month}"
        gen_col = f"Netgen {month}"
        if mcf_col not in rows.columns:
            continue
        frame = rows[["Plant Id", "Plant Name", mcf_col, mmbtu_col, gen_col]].copy()
        frame["gas_consumed_mcf"] = clean_number(frame[mcf_col])
        frame["gas_consumed_mmbtu"] = clean_number(frame[mmbtu_col])
        frame["net_generation_mwh"] = clean_number(frame[gen_col])
        frame = frame[frame[["gas_consumed_mcf", "gas_consumed_mmbtu", "net_generation_mwh"]].notna().any(axis=1)]
        if frame.empty:
            continue
        grouped = frame.groupby(["Plant Id", "Plant Name"], dropna=False)[
            ["gas_consumed_mcf", "gas_consumed_mmbtu", "net_generation_mwh"]
        ].sum(min_count=1).reset_index()
        grouped["month"] = f"{year}-{month_number:02d}"
        records.append(grouped)
    if not records:
        return pd.DataFrame(columns=["eia_plant_id_orispl", "plant_name", "month", "gas_consumed_mcf", "gas_consumed_mmbtu", "net_generation_mwh"]), str(title)
    demand = pd.concat(records, ignore_index=True)
    demand = demand.rename(columns={"Plant Id": "eia_plant_id_orispl", "Plant Name": "plant_name"})
    return demand[["eia_plant_id_orispl", "plant_name", "month", "gas_consumed_mcf", "gas_consumed_mmbtu", "net_generation_mwh"]], str(title)


def write_outputs(output_dir: Path, plants: pd.DataFrame, demand: pd.DataFrame, metadata: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plants = plants.sort_values(["gas_summer_capacity_mw", "plant_name"], ascending=[False, True])
    demand = demand.sort_values(["month", "plant_name"])
    latest_month = demand["month"].max() if not demand.empty else None
    current_month = demand[demand["month"] == latest_month].copy() if latest_month else demand.copy()
    latest_by_plant = (
        demand.sort_values("month").drop_duplicates("eia_plant_id_orispl", keep="last")
        if not demand.empty else demand.copy()
    )
    latest_by_plant = latest_by_plant.drop(columns=["plant_name"]).rename(columns={"month": "demand_as_of_month"})
    combined = plants.merge(latest_by_plant, on="eia_plant_id_orispl", how="left")
    combined["demand_reporting_status"] = combined["demand_as_of_month"].apply(
        lambda value: "current_month" if value == latest_month else ("latest_annual_or_prior" if pd.notna(value) else "no_eia_923_match")
    )
    plants.to_csv(output_dir / "ercot_gas_plants.csv", index=False, float_format="%.3f")
    demand.to_csv(output_dir / "ercot_gas_demand_monthly.csv", index=False, float_format="%.3f")
    combined.to_csv(output_dir / "ercot_gas_plants_latest.csv", index=False, float_format="%.3f")
    summary = {
        "as_of_month": latest_month,
        "capacity_inventory_plant_count": int(len(plants)),
        "gas_unit_count": int(plants["gas_unit_count"].sum()),
        "gas_nameplate_capacity_mw": round(float(plants["gas_nameplate_capacity_mw"].sum()), 3),
        "gas_summer_capacity_mw": round(float(plants["gas_summer_capacity_mw"].sum()), 3),
        "latest_month_reporting_plant_count": int(current_month["eia_plant_id_orispl"].nunique()) if latest_month else 0,
        "capacity_plants_with_any_demand_match": int(combined["demand_as_of_month"].notna().sum()),
        "capacity_site_demand_coverage_pct": round(float(combined["demand_as_of_month"].notna().mean() * 100), 3),
        "capacity_mw_with_any_demand_match": round(float(combined.loc[combined["demand_as_of_month"].notna(), "gas_summer_capacity_mw"].sum()), 3),
        "capacity_mw_demand_coverage_pct": round(float(combined.loc[combined["demand_as_of_month"].notna(), "gas_summer_capacity_mw"].sum() / plants["gas_summer_capacity_mw"].sum() * 100), 3),
        "latest_month_gas_consumed_mcf": round(float(current_month["gas_consumed_mcf"].sum()), 3) if latest_month else None,
        "latest_month_gas_consumed_mmbtu": round(float(current_month["gas_consumed_mmbtu"].sum()), 3) if latest_month else None,
        "latest_month_net_generation_mwh": round(float(current_month["net_generation_mwh"].sum()), 3) if latest_month else None,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--capacity-file", type=Path)
    parser.add_argument("--demand-file", type=Path)
    parser.add_argument("--annual-demand-file", type=Path)
    parser.add_argument("--capacity-source-url")
    parser.add_argument("--demand-source-url")
    parser.add_argument("--annual-demand-source-url")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        capacity_path = args.capacity_file or temp / "eia860m.xlsx"
        demand_path = args.demand_file or temp / "eia923_current.zip"
        annual_demand_path = args.annual_demand_file or temp / "eia923_prior_final.zip"
        capacity_url = None
        demand_url = None
        sources = {}
        if not args.capacity_file:
            sources["eia_860m"] = latest_available_download(
                EIA_860M_PAGE, r"/[a-z]+_generator20\d{2}\.xlsx$", capacity_path
            )
        else:
            sources["eia_860m"] = file_metadata(capacity_path, args.capacity_source_url)
        if not args.demand_file and not args.annual_demand_file:
            demand_sources = latest_available_downloads(
                EIA_923_PAGE, r"/f923_20\d{2}\.zip$", [demand_path, annual_demand_path]
            )
            sources["eia_923_current"] = demand_sources[0]
            sources["eia_923_prior_final"] = demand_sources[1]
        else:
            if not args.demand_file or not args.annual_demand_file:
                raise ValueError("--demand-file and --annual-demand-file must be provided together")
            sources["eia_923_current"] = file_metadata(demand_path, args.demand_source_url)
            sources["eia_923_prior_final"] = file_metadata(annual_demand_path, args.annual_demand_source_url)
        plants, capacity_title = read_capacity(capacity_path)
        demand_current, demand_title = read_demand(demand_path)
        demand_annual, annual_demand_title = read_demand(annual_demand_path)
        demand = pd.concat([demand_annual, demand_current], ignore_index=True)
        demand = demand.sort_values("month").drop_duplicates(["eia_plant_id_orispl", "month"], keep="last")
        metadata = {
            "scope": {"balancing_authority_code": ERCOT_BA_CODE, "fuel_codes": sorted(GAS_FUEL_CODES)},
            "capacity_source_title": capacity_title,
            "demand_source_title": demand_title,
            "annual_demand_source_title": annual_demand_title,
            "sources": sources,
            "source_pages": {
                "eia_860m": EIA_860M_PAGE,
                "eia_923": EIA_923_PAGE,
                "ercot_capacity_cross_check": ERCOT_CAPACITY_PAGE,
            },
        }
        write_outputs(Path(args.output_dir), plants, demand, metadata)


if __name__ == "__main__":
    main()
