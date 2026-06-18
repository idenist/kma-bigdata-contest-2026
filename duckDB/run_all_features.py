from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pffdri_common import default_duckdb_path, project_path, project_root


SCRIPT_ORDER = [
    "01_weather_features.py",
    "02_forest_features.py",
    "03_terrain_ywi_features.py",
    "04_power_access_pei_features.py",
    "05_index_features.py",
    "06_fire_target.py",
    "99_build_final_dataset.py",
]
LOAD_SCRIPT = "00_load_grid_date_master.py"

DATE_FILTER_SCRIPTS = {
    "01_weather_features.py",
    "03_terrain_ywi_features.py",
    "05_index_features.py",
    "99_build_final_dataset.py",
}


def format_seconds(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def remove_if_exists(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def discover_months(source_grid_date: Path) -> list[str]:
    if source_grid_date.is_file():
        month = next((parent.name.removeprefix("month=") for parent in source_grid_date.parents if parent.name.startswith("month=")), None)
        return [month] if month else []
    if not source_grid_date.exists():
        return []
    months = sorted(path.name.removeprefix("month=") for path in source_grid_date.glob("month=*") if path.is_dir())
    return [month for month in months if len(month) == 7]


def build_chunks(args: argparse.Namespace, source_grid_date: Path) -> list[tuple[str, list[str] | None]]:
    if args.chunk_by == "none":
        return [("all", args.months)]

    months = sorted(args.months) if args.months else discover_months(source_grid_date)
    if not months:
        return [("all", args.months)]

    if args.chunk_by == "month":
        return [(month, [month]) for month in months]

    years: dict[str, list[str]] = {}
    for month in months:
        years.setdefault(month[:4], []).append(month)
    return [(year, year_months) for year, year_months in sorted(years.items())]


def apply_chunk(args: argparse.Namespace, months: list[str] | None) -> argparse.Namespace:
    chunk_args = argparse.Namespace(**vars(args))
    chunk_args.months = months
    return chunk_args


def add_date_filters(cmd: list[str], args: argparse.Namespace) -> None:
    if args.months:
        cmd += ["--months", *args.months]
    if args.start_date:
        cmd += ["--start-date", args.start_date]
    if args.end_date:
        cmd += ["--end-date", args.end_date]


def add_runtime_options(cmd: list[str], args: argparse.Namespace) -> None:
    cmd += [
        "--duckdb-path",
        str(args.duckdb_path),
        "--threads",
        str(args.threads),
        "--memory-limit",
        args.memory_limit,
    ]


def add_parquet_options(cmd: list[str], args: argparse.Namespace, append: bool = False) -> None:
    cmd += [
        "--parquet-compression",
        args.parquet_compression,
        "--parquet-compression-level",
        str(args.parquet_compression_level),
    ]
    if args.overwrite_parquet:
        cmd.append("--overwrite-parquet")
    if append:
        cmd.append("--append-parquet")


def run_command(cmd: list[str], label: str) -> None:
    print("\n" + "=" * 80, flush=True)
    print(f"[START] {label}", flush=True)
    print("[RUN]", " ".join(cmd), flush=True)
    print("=" * 80, flush=True)
    started_at = time.perf_counter()
    subprocess.run(cmd, check=True)
    elapsed = time.perf_counter() - started_at
    print(f"[DONE] {label} elapsed={format_seconds(elapsed)}", flush=True)


def build_command(
    script_name: str,
    script_dir: Path,
    root: Path,
    args: argparse.Namespace,
    source_grid_date: Path,
    weather_parquet: Path,
    forest_parquet: Path,
    terrain_parquet: Path,
    power_parquet: Path,
    index_parquet: Path,
    final_parquet: Path,
) -> list[str]:
    cmd = [sys.executable, str(script_dir / script_name)]
    if script_name != "06_fire_target.py":
        add_runtime_options(cmd, args)

    if script_name == LOAD_SCRIPT:
        cmd += ["--max-part", str(args.max_part)]
        if args.parquet_root:
            cmd += ["--parquet-root", str(source_grid_date)]

    if script_name == "06_fire_target.py":
        cmd += ["--output-dir", str(root / "output" / "target")]

    if script_name in DATE_FILTER_SCRIPTS:
        add_date_filters(cmd, args)

    if args.mode == "parquet" and script_name == "01_weather_features.py":
        cmd += [
            "--source-parquet",
            str(source_grid_date),
            "--parquet-only",
            "--export-parquet-dir",
            str(weather_parquet),
        ]
        add_parquet_options(cmd, args, append=args.append_partitioned_parquet)

    if args.mode == "parquet" and script_name == "02_forest_features.py":
        cmd += ["--parquet-only", "--export-parquet", str(forest_parquet)]
        add_parquet_options(cmd, args)

    if args.mode == "parquet" and script_name == "03_terrain_ywi_features.py":
        cmd += [
            "--weather-parquet",
            str(weather_parquet),
            "--parquet-only",
            "--export-parquet-dir",
            str(terrain_parquet),
        ]
        add_parquet_options(cmd, args, append=args.append_partitioned_parquet)

    if args.mode == "parquet" and script_name == "04_power_access_pei_features.py":
        cmd += ["--parquet-only", "--export-parquet", str(power_parquet)]
        add_parquet_options(cmd, args)

    if args.mode == "parquet" and script_name == "05_index_features.py":
        cmd += [
            "--weather-parquet",
            str(weather_parquet),
            "--forest-parquet",
            str(forest_parquet),
            "--terrain-parquet",
            str(terrain_parquet),
            "--power-parquet",
            str(power_parquet),
            "--parquet-only",
            "--export-parquet-dir",
            str(index_parquet),
        ]
        add_parquet_options(cmd, args, append=args.append_partitioned_parquet)

    if script_name == "99_build_final_dataset.py":
        if args.without_target:
            cmd.append("--without-target")
        if args.mode == "parquet":
            cmd += [
                "--weather-parquet",
                str(weather_parquet),
                "--forest-parquet",
                str(forest_parquet),
                "--terrain-parquet",
                str(terrain_parquet),
                "--power-parquet",
                str(power_parquet),
                "--index-parquet",
                str(index_parquet),
                "--parquet-only",
                "--export-parquet-dir",
                str(final_parquet),
            ]
            add_parquet_options(cmd, args, append=args.append_partitioned_parquet)

    return cmd


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    root = project_root(__file__)

    parser = argparse.ArgumentParser(description="Run all P-FFDRI feature generation steps.")
    parser.add_argument("--mode", choices=["duckdb", "parquet"], default="duckdb")
    parser.add_argument("--duckdb-path", default=str(default_duckdb_path(__file__)))
    parser.add_argument("--include-load", action="store_true", help="Run 00_load_grid_date_master.py before features.")
    parser.add_argument("--parquet-root", help="Input root for 00_load_grid_date_master.py.")
    parser.add_argument("--max-part", type=int, default=721)
    parser.add_argument("--stage-root", default=str(Path("output") / "stage"))
    parser.add_argument("--final-root", default=str(Path("output") / "final"))
    parser.add_argument("--months", nargs="*", help="Optional months, e.g. 2025-03 2025-04")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--chunk-by", choices=["none", "month", "year"], help="Progress chunk unit. Default: month in parquet mode, none in duckdb mode.")
    parser.add_argument("--without-target", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-limit", default="28GB")
    parser.add_argument("--parquet-compression", default="ZSTD", choices=["ZSTD", "SNAPPY", "GZIP", "BROTLI", "LZ4"])
    parser.add_argument("--parquet-compression-level", type=int, default=6)
    parser.add_argument("--overwrite-parquet", action="store_true")
    args = parser.parse_args()

    args.duckdb_path = project_path(__file__, args.duckdb_path)
    if args.chunk_by is None:
        args.chunk_by = "month" if args.mode == "parquet" else "none"
    args.append_partitioned_parquet = args.mode == "parquet" and args.chunk_by != "none"
    stage_root = project_path(__file__, args.stage_root)
    final_root = project_path(__file__, args.final_root)
    source_grid_date = project_path(__file__, args.parquet_root) if args.parquet_root else root / "data" / "grid_date_master"
    weather_parquet = stage_root / "feat_weather_daily"
    forest_parquet = stage_root / "feat_forest_static.parquet"
    terrain_parquet = stage_root / "feat_terrain_ywi_daily"
    power_parquet = stage_root / "feat_power_access_static.parquet"
    index_parquet = stage_root / "feat_pffdri_daily"
    final_parquet = final_root / "final_feature_daily"

    if args.mode == "parquet" and args.chunk_by != "none":
        if args.overwrite_parquet:
            cleanup_targets = [
                weather_parquet,
                terrain_parquet,
                index_parquet,
                final_parquet,
            ]
            print("[CLEANUP] overwrite requested; removing partitioned output roots once", flush=True)
            for path in cleanup_targets:
                remove_if_exists(path)
                print(f"[CLEANUP] removed if existed: {path}", flush=True)

        static_scripts = ["02_forest_features.py", "04_power_access_pei_features.py"]
        for index, script_name in enumerate(static_scripts, 1):
            cmd = build_command(
                script_name,
                script_dir,
                root,
                args,
                source_grid_date,
                weather_parquet,
                forest_parquet,
                terrain_parquet,
                power_parquet,
                index_parquet,
                final_parquet,
            )
            run_command(cmd, f"static {index}/{len(static_scripts)} {script_name}")

        chunks = build_chunks(args, source_grid_date)
        chunk_scripts = ["01_weather_features.py", "03_terrain_ywi_features.py", "05_index_features.py", "99_build_final_dataset.py"]
        total_tasks = len(chunks) * len(chunk_scripts)
        task_no = 0
        pipeline_started_at = time.perf_counter()
        for chunk_no, (chunk_label, chunk_months) in enumerate(chunks, 1):
            chunk_args = apply_chunk(args, chunk_months)
            print("\n" + "#" * 80, flush=True)
            print(f"[CHUNK] {chunk_no}/{len(chunks)} {args.chunk_by}={chunk_label} months={chunk_months or 'all'}", flush=True)
            print("#" * 80, flush=True)
            for script_name in chunk_scripts:
                task_no += 1
                cmd = build_command(
                    script_name,
                    script_dir,
                    root,
                    chunk_args,
                    source_grid_date,
                    weather_parquet,
                    forest_parquet,
                    terrain_parquet,
                    power_parquet,
                    index_parquet,
                    final_parquet,
                )
                run_command(cmd, f"task {task_no}/{total_tasks} chunk={chunk_label} {script_name}")
        print(f"[PIPELINE DONE] chunks={len(chunks)} tasks={total_tasks} elapsed={format_seconds(time.perf_counter() - pipeline_started_at)}", flush=True)
        return

    script_order = [LOAD_SCRIPT, *SCRIPT_ORDER] if args.include_load and args.mode == "duckdb" else SCRIPT_ORDER
    total_scripts = len([script for script in script_order if not (args.without_target and script == "06_fire_target.py")])
    script_no = 0
    pipeline_started_at = time.perf_counter()
    for script_name in script_order:
        if args.without_target and script_name == "06_fire_target.py":
            continue
        script_no += 1
        cmd = build_command(
            script_name,
            script_dir,
            root,
            args,
            source_grid_date,
            weather_parquet,
            forest_parquet,
            terrain_parquet,
            power_parquet,
            index_parquet,
            final_parquet,
        )
        run_command(cmd, f"script {script_no}/{total_scripts} {script_name}")
    print(f"[PIPELINE DONE] scripts={total_scripts} elapsed={format_seconds(time.perf_counter() - pipeline_started_at)}", flush=True)


if __name__ == "__main__":
    main()
