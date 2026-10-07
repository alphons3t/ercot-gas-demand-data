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
        return pd.DataFrame(columns=["plant_id", "plant_name", "month", "gas_consumed_mcf", "gas_consumed_mmbtu", "net_generation_mwh"]), str(title)
    demand = pd.concat(records, ignore_index=True)
    demand = demand.rename(columns={"Plant Id": "plant_id", "Plant Name": "plant_name"})
    return demand[["plant_id", "plant_name", "month", "gas_consumed_mcf", "gas_consumed_mmbtu", "net_generation_mwh"]], str(title)


def write_outputs(output_dir: Path, plants: pd.DataFrame, demand: pd.DataFrame, metadata: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    plants = plants.sort_values(["gas_summer_capacity_mw", "plant_name"], ascending=[False, True])
    demand = demand.sort_values(["month", "plant_name"])
    latest_month = demand["month"].max() if not demand.empty else None
    latest = demand[demand["month"] == latest_month].drop(columns=["plant_name"]) if latest_month else demand
    combined = plants.merge(latest, on="plant_id", how="left")
    plants.to_csv(output_dir / "ercot_gas_plants.csv", index=False, float_format="%.3f")
    demand.to_csv(output_dir / "ercot_gas_demand_monthly.csv", index=False, float_format="%.3f")
    combined.to_csv(output_dir / "ercot_gas_plants_latest.csv", index=False, float_format="%.3f")
    summary = {
        "as_of_month": latest_month,
        "plant_count": int(len(plants)),
        "gas_unit_count": int(plants["gas_unit_count"].sum()),
        "gas_nameplate_capacity_mw": round(float(plants["gas_nameplate_capacity_mw"].sum()), 3),
        "gas_summer_capacity_mw": round(float(plants["gas_summer_capacity_mw"].sum()), 3),
        "latest_month_gas_consumed_mcf": round(float(latest["gas_consumed_mcf"].sum()), 3) if latest_month else None,
        "latest_month_gas_consumed_mmbtu": round(float(latest["gas_consumed_mmbtu"].sum()), 3) if latest_month else None,
        "latest_month_net_generation_mwh": round(float(latest["net_generation_mwh"].sum()), 3) if latest_month else None,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--capacity-file", type=Path)
    parser.add_argument("--demand-file", type=Path)
    parser.add_argument("--capacity-source-url")
    parser.add_argument("--demand-source-url")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        capacity_path = args.capacity_file or temp / "eia860m.xlsx"
        demand_path = args.demand_file or temp / "eia923.zip"
        capacity_url = None
        demand_url = None
        sources = {}
        if not args.capacity_file:
            sources["eia_860m"] = latest_available_download(
                EIA_860M_PAGE, r"/[a-z]+_generator20\d{2}\.xlsx$", capacity_path
            )
        else:
            sources["eia_860m"] = file_metadata(capacity_path, args.capacity_source_url)
        if not args.demand_file:
            sources["eia_923"] = latest_available_download(
                EIA_923_PAGE, r"/f923_20\d{2}\.zip$", demand_path
            )
        else:
            sources["eia_923"] = file_metadata(demand_path, args.demand_source_url)
        plants, capacity_title = read_capacity(capacity_path)
        demand, demand_title = read_demand(demand_path)
        metadata = {
            "scope": {"balancing_authority_code": ERCOT_BA_CODE, "fuel_codes": sorted(GAS_FUEL_CODES)},
            "capacity_source_title": capacity_title,
            "demand_source_title": demand_title,
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
