from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_ORDER = [
    "01_weather_features.py",
    "02_forest_features.py",
    "03_terrain_ywi_features.py",
    "04_power_access_pei_features.py",
    "05_index_features.py",
    "06_fire_target.py",
    "99_build_final_dataset.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all P-FFDRI feature generation steps.")
    parser.add_argument("--months", nargs="*", help="Optional months, e.g. 2025-03 2025-04")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--without-target", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent

    for script_name in SCRIPT_ORDER:
        cmd = [sys.executable, str(script_dir / script_name)]
        if args.months and script_name in {
            "01_weather_features.py",
            "03_terrain_ywi_features.py",
            "05_index_features.py",
            "99_build_final_dataset.py",
        }:
            cmd += ["--months", *args.months]
        if args.start_date and script_name in {
            "01_weather_features.py",
            "03_terrain_ywi_features.py",
            "05_index_features.py",
            "99_build_final_dataset.py",
        }:
            cmd += ["--start-date", args.start_date]
        if args.end_date and script_name in {
            "01_weather_features.py",
            "03_terrain_ywi_features.py",
            "05_index_features.py",
            "99_build_final_dataset.py",
        }:
            cmd += ["--end-date", args.end_date]
        if args.without_target and script_name == "99_build_final_dataset.py":
            cmd += ["--without-target"]

        print("\n" + "=" * 80)
        print("[RUN]", " ".join(cmd))
        print("=" * 80)
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
