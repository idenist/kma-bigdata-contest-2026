from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MASTER_GRID = Path("data") / "master_grid.parquet"
DEFAULT_FIRE_HISTORY = Path("data") / "(공통데이터)산불발생이력데이터_forest_fire_all_4326.csv"
DEFAULT_OUTPUT_DIR = Path("output") / "target"
DEFAULT_GRID_SIZES = (500, 1000, 2000)


def project_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def parse_grid_sizes(value: str) -> list[int]:
    grid_sizes = []
    for item in value.split(","):
        item = item.strip().lower().replace("m", "")
        if not item:
            continue
        grid_size = int(item)
        if grid_size % 100 != 0:
            raise ValueError(f"grid size must be a multiple of 100m: {grid_size}")
        grid_sizes.append(grid_size)
    if not grid_sizes:
        raise ValueError("at least one grid size is required")
    return grid_sizes


def grid_size_label(grid_size: int) -> str:
    return f"{grid_size // 1000}km" if grid_size % 1000 == 0 else f"{grid_size}m"


def read_fire_history(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"failed to decode {path} with utf-8-sig/cp949/euc-kr: {last_error}",
    )


def normalize_fire_history(
    fire_history: pd.DataFrame,
    start_year: int,
    end_year: int,
    start_date: str | None,
    end_date: str | None,
) -> pd.DataFrame:
    required = {"objt_id", "occu_date", "occu_year", "longitude", "latitude", "adres"}
    missing = sorted(required.difference(fire_history.columns))
    if missing:
        raise KeyError(f"fire history missing required columns: {missing}")

    fire = fire_history.copy()
    fire["date"] = pd.to_datetime(fire["occu_date"], errors="coerce").dt.date
    fire["occu_year"] = pd.to_numeric(fire["occu_year"], errors="coerce")
    fire["longitude"] = pd.to_numeric(fire["longitude"], errors="coerce")
    fire["latitude"] = pd.to_numeric(fire["latitude"], errors="coerce")
    if "amount" in fire.columns:
        fire["amount"] = pd.to_numeric(fire["amount"], errors="coerce")
    else:
        fire["amount"] = np.nan

    address = fire["adres"].fillna("").astype(str)
    ctprvn = pd.to_numeric(fire.get("ctprvn_cd"), errors="coerce") if "ctprvn_cd" in fire.columns else pd.Series(np.nan, index=fire.index)
    gangwon_mask = address.str.contains("강원", regex=False) | ctprvn.isin([42, 51])
    year_mask = fire["occu_year"].between(start_year, end_year, inclusive="both")
    coord_mask = fire["longitude"].notna() & fire["latitude"].notna()
    date_mask = fire["date"].notna()

    fire = fire.loc[gangwon_mask & year_mask & coord_mask & date_mask].copy()
    if start_date:
        fire = fire.loc[fire["date"] >= pd.Timestamp(start_date).date()].copy()
    if end_date:
        fire = fire.loc[fire["date"] <= pd.Timestamp(end_date).date()].copy()
    fire["objt_id"] = fire["objt_id"].astype(str)
    fire["adres"] = fire["adres"].fillna("").astype(str)
    return fire


def add_epsg5179_coordinates(fire: pd.DataFrame) -> pd.DataFrame:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x5179, y5179 = transformer.transform(fire["longitude"].to_numpy(), fire["latitude"].to_numpy())
    fire = fire.copy()
    fire["x5179"] = x5179
    fire["y5179"] = y5179
    return fire


def load_master_grid(path: Path) -> pd.DataFrame:
    required = {"grid_id", "grid_x", "grid_y"}
    master = pd.read_parquet(path, columns=sorted(required))
    missing = sorted(required.difference(master.columns))
    if missing:
        raise KeyError(f"master grid missing required columns: {missing}")

    master = master.drop_duplicates("grid_id").copy()
    master["grid_x"] = pd.to_numeric(master["grid_x"], errors="raise").astype("int64")
    master["grid_y"] = pd.to_numeric(master["grid_y"], errors="raise").astype("int64")
    return master


def build_fire_cell_summary(fire: pd.DataFrame, grid_size: int) -> pd.DataFrame:
    fire = fire.copy()
    fire["target_grid_size_m"] = grid_size
    fire["target_grid_x"] = np.floor(fire["x5179"] / grid_size).astype("int64")
    fire["target_grid_y"] = np.floor(fire["y5179"] / grid_size).astype("int64")
    fire["target_grid_id"] = (
        "epsg5179_"
        + grid_size_label(grid_size)
        + "_"
        + fire["target_grid_x"].astype(str)
        + "_"
        + fire["target_grid_y"].astype(str)
    )

    grouped = (
        fire.groupby(["date", "target_grid_size_m", "target_grid_x", "target_grid_y", "target_grid_id"], as_index=False)
        .agg(
            fire_count=("objt_id", "count"),
            fire_objt_ids=("objt_id", lambda values: "|".join(sorted(set(values)))),
            fire_addresses=("adres", lambda values: "|".join(sorted(set(v for v in values if v)))),
            fire_amount_sum=("amount", "sum"),
            event_lon_mean=("longitude", "mean"),
            event_lat_mean=("latitude", "mean"),
            event_x5179_mean=("x5179", "mean"),
            event_y5179_mean=("y5179", "mean"),
        )
    )
    grouped["fire_label"] = 1
    return grouped


def build_grid_mapping(master_grid: pd.DataFrame, grid_size: int) -> pd.DataFrame:
    mapping = master_grid.copy()
    mapping["target_grid_size_m"] = grid_size
    mapping["target_grid_x"] = np.floor((mapping["grid_x"] * 100) / grid_size).astype("int64")
    mapping["target_grid_y"] = np.floor((mapping["grid_y"] * 100) / grid_size).astype("int64")
    return mapping[["target_grid_size_m", "target_grid_x", "target_grid_y", "grid_id"]]


def build_mapped_target(fire: pd.DataFrame, master_grid: pd.DataFrame, grid_size: int) -> pd.DataFrame:
    fire_cells = build_fire_cell_summary(fire, grid_size)
    grid_mapping = build_grid_mapping(master_grid, grid_size)
    mapped = fire_cells.merge(
        grid_mapping,
        on=["target_grid_size_m", "target_grid_x", "target_grid_y"],
        how="inner",
        validate="many_to_many",
    )
    mapped = mapped[
        [
            "date",
            "grid_id",
            "target_grid_id",
            "target_grid_size_m",
            "target_grid_x",
            "target_grid_y",
            "fire_label",
            "fire_count",
            "fire_objt_ids",
            "fire_addresses",
            "fire_amount_sum",
            "event_lon_mean",
            "event_lat_mean",
            "event_x5179_mean",
            "event_y5179_mean",
        ]
    ].sort_values(["date", "target_grid_id", "grid_id"], kind="stable")
    return mapped


def write_parquet(df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False, compression="zstd")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create daily Gangwon fire targets mapped to existing grid_id at multiple EPSG:5179 grid sizes."
    )
    parser.add_argument("--master-grid", default=str(DEFAULT_MASTER_GRID))
    parser.add_argument("--fire-history", default=str(DEFAULT_FIRE_HISTORY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--grid-sizes", default=",".join(str(size) for size in DEFAULT_GRID_SIZES))
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--start-date", default="2020-02-01")
    parser.add_argument("--end-date", default="2024-12-31")
    args = parser.parse_args()

    master_grid_path = project_path(args.master_grid)
    fire_history_path = project_path(args.fire_history)
    output_dir = project_path(args.output_dir)
    grid_sizes = parse_grid_sizes(args.grid_sizes)

    raw_fire = read_fire_history(fire_history_path)
    fire = normalize_fire_history(raw_fire, args.start_year, args.end_year, args.start_date, args.end_date)
    fire = add_epsg5179_coordinates(fire)
    master_grid = load_master_grid(master_grid_path)

    print(f"[INPUT] fire_history_rows={len(raw_fire):,}")
    print(f"[FILTER] gangwon_{args.start_year}_{args.end_year}_rows={len(fire):,}")
    print(f"[GRID] master_grid_ids={master_grid['grid_id'].nunique():,}")

    for index, grid_size in enumerate(grid_sizes, 1):
        print(f"[PROGRESS] grid_size {index}/{len(grid_sizes)} {grid_size_label(grid_size)}")
        target = build_mapped_target(fire, master_grid, grid_size)
        label = grid_size_label(grid_size)
        output_path = output_dir / f"target_fire_daily_{label}.parquet"
        write_parquet(target, output_path)
        print(
            f"[EXPORT] {label}: rows={len(target):,}, "
            f"dates={target['date'].nunique():,}, "
            f"grid_ids={target['grid_id'].nunique():,}, "
            f"target_cells={target['target_grid_id'].nunique():,}, "
            f"path={output_path}"
        )


if __name__ == "__main__":
    main()
