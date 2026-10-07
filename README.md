# ERCOT gas plant demand and capacity

An automatically refreshed, plant-level dataset for natural-gas generation in the ERCOT balancing authority.

## Data files

- `data/ercot_gas_plants.csv`: operating gas-fired plant locations and capacity aggregated from generator records.
- `data/ercot_gas_demand_monthly.csv`: monthly plant gas consumption and net generation.
- `data/ercot_gas_plants_latest.csv`: plant capacity joined to the latest available demand month.
- `data/summary.json`: ERCOT-wide totals for quick use.
- `data/metadata.json`: source files, checksums, scope, and refresh time.

Important units:

- Capacity is in megawatts (MW).
- Gas demand is EIA electric-generation fuel consumption in thousand cubic feet (`mcf`) and million British thermal units (`MMBtu`). EIA's source label is `mcf`; in this dataset that means thousand cubic feet.
- Generation is in megawatt-hours (MWh).

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
4. Download the latest [EIA-923 monthly plant fuel data](https://www.eia.gov/electricity/data/eia923/).
5. Keep `ERCO` natural-gas records and aggregate electric-generation fuel consumption by plant and month.
6. Track [ERCOT resource-capacity reports](https://www.ercot.com/gridinfo/resource) as an external cross-check.

## Coverage and limitations

- The repository represents plants assigned by EIA to the ERCOT balancing authority, not every gas generator physically located in Texas.
- EIA plant-level demand is monthly and published with a lag. Public real-time ERCOT data reports system generation by fuel, not verified plant-level gas consumption. The updater therefore checks daily but only changes demand when EIA publishes new data.
- Capacity is preliminary in EIA-860M and can be revised in later releases.
- A plant can have non-gas units; capacity here includes only generators whose reported primary energy source is natural gas.
- CHP fuel use is represented by EIA's quantity consumed for electricity, not total facility fuel use.

## License

Code is released under the MIT License. U.S. government EIA data is generally public domain; ERCOT source terms remain applicable to ERCOT materials.

