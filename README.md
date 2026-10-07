# ERCOT gas plant demand and capacity

An automatically refreshed, plant-level dataset for natural-gas generation in the ERCOT balancing authority.

## Data files

- `data/ercot_gas_plants.csv`: operating gas-fired plant locations and capacity aggregated from generator records. `eia_plant_id_orispl` is the explicit EIA Plant ID/ORISPL key.
- `data/ercot_gas_generators.csv`: generator-level ORISPL, generator ID, prime mover, technology, nameplate/net capacities, operating year, and planned retirement year.
- `data/ercot_gas_demand_monthly.csv`: monthly plant gas consumption and net generation, combining the latest complete annual EIA-923 release with the current partial monthly release.
- `data/ercot_gas_plants_latest.csv`: plant capacity joined to each plant's latest available demand observation. `demand_reporting_status` distinguishes current-month, prior-period, and unmatched records.
- `data/summary.json`: ERCOT-wide totals for quick use.
- `data/metadata.json`: source titles, URLs, checksums, and filter scope.

Important units:

- Capacity is in megawatts (MW).
- Gas demand is EIA electric-generation fuel consumption in thousand cubic feet (`mcf`) and million British thermal units (`MMBtu`). EIA's source label is `mcf`; in this dataset that means thousand cubic feet.
- Generation is in megawatt-hours (MWh).
- `heat_rate_mmbtu_per_mwh` is calculated only when monthly net generation is positive.
- `capacity_factor` is monthly net generation divided by `capacity_factor_basis_summer_mw` times calendar hours in the month. It uses current EIA-860M capacity, so historical comparisons should be treated cautiously. It is unavailable for demand records that do not match the current gas-capacity inventory.
- `tech_ccgt_mw`, `tech_ct_mw`, and `tech_steam_mw` allocate generator summer MW using EIA technology labels. Other gas technologies remain in total capacity but not those three normalized buckets.
- `ercot_footprint` is a geographic flag: Texas is `true`; the Oklahoma ERCO plant is retained and flagged `false`.
- Every CSV row includes `source_file`, `source_url`, and UTC `retrieved_at` provenance.

## Update agent

The GitHub Actions workflow checks official sources every day at 11:17 UTC. It commits only when the generated data changes. You can also run it from the Actions tab with **Run workflow**.

To refresh locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/update_data.py
```

## Method

1. Download the latest [EIA-860M generator inventory](https://www.eia.gov/electricity/data/eia860m/).
2. Keep operating generators with balancing authority code `ERCO` and energy source code `NG`.
3. Aggregate generator capacity to the EIA plant ID while retaining location and technology.
4. Download both the current partial and latest prior complete [EIA-923 plant fuel datasets](https://www.eia.gov/electricity/data/eia923/).
5. Keep `ERCO` natural-gas records, aggregate electric-generation fuel consumption by plant and month, and prefer the current release where records overlap.
6. Track [ERCOT resource-capacity reports](https://www.ercot.com/gridinfo/resource) as an external cross-check.

## Coverage and limitations

- The repository represents plants assigned by EIA to the ERCOT balancing authority, not every gas generator physically located in Texas.
- EIA plant-level demand is monthly and published with a lag. The current-year monthly file covers only monthly survey respondents; the prior complete annual release fills most smaller-plant gaps. Public real-time ERCOT data does not provide verified plant-level gas consumption.
- `summary.json` reports site and capacity-weighted demand coverage. A plant's `demand_as_of_month` must be used before comparing plants with different reporting freshness.
- EIA balancing-authority capacity is not definitionally identical to ERCOT's published market-resource totals. The repository does not present either as a coverage denominator for the other.
- Capacity is preliminary in EIA-860M and can be revised in later releases.
- A plant can have non-gas units; capacity here includes only generators whose reported primary energy source is natural gas.
- CHP fuel use is represented by EIA's quantity consumed for electricity, not total facility fuel use.

## License

Code is released under the MIT License. U.S. government EIA data is generally public domain; ERCOT source terms remain applicable to ERCOT materials.

## Verdict

> ***Independent audit:** Real monthly EIA-923 operational data Jan 2025-Jul 2026. Fuel-gen correlation 0.984, 890 distinct capacity factors, CCGT median CF 58.5%, seasonality 90M -> 209M MMBtu Mar->Aug. 100% geocoded, 99.3% unique coords, 0.7% co-located HEB. 5/5 random plants pass ORIS/coords/capacity checks max delta 3.98%. 280-283 plants/mo in 2025 vs 74-77 in 2026 reflects EIA preliminary lag, not fleet loss. Not capacity \* hours synthetic.*
