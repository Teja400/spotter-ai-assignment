import pandas as pd

INPUT_FILE = "fuel-prices-for-be-assessment.csv"
OUTPUT_FILE = "fuel_prices_cleaned.csv"

ID_COLUMN = "OPIS Truckstop ID"
PRICE_COLUMN = "Retail Price"

LOCATION_COLUMNS = [
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
]


def clean_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove leading/trailing and repeated whitespace from text columns."""
    for column in LOCATION_COLUMNS:
        if column in df.columns:
            df[column] = (
                df[column]
                .astype("string")
                .str.replace(r"\s+", " ", regex=True)
                .str.strip()
            )

    return df


def validate_columns(df: pd.DataFrame) -> None:
    """Make sure the expected columns exist."""
    required_columns = [
        ID_COLUMN,
        *LOCATION_COLUMNS,
        PRICE_COLUMN,
    ]

    missing_columns = [
        column for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}"
        )


def validate_station_data(df: pd.DataFrame) -> None:
    """
    Verify that one OPIS Truckstop ID consistently represents
    one station/location.
    """

    print("\nChecking station consistency...")

    for column in LOCATION_COLUMNS:
        unique_values = (
            df.groupby(ID_COLUMN)[column]
            .nunique(dropna=False)
        )

        inconsistent = unique_values[unique_values > 1]

        if len(inconsistent) > 0:
            print(
                f"WARNING: {len(inconsistent):,} OPIS IDs "
                f"have multiple values for '{column}'"
            )
        else:
            print(f"OK: {column}")


def aggregate_prices(df: pd.DataFrame) -> pd.DataFrame:
    """
    Consolidate multiple records belonging to the same
    OPIS Truckstop ID.

    Retail Price is averaged because the dataset contains
    no timestamp indicating which observation is the latest.
    """

    # Keep the station attributes from the first record.
    station_columns = [
        ID_COLUMN,
        *LOCATION_COLUMNS,
    ]

    station_info = (
        df[station_columns]
        .drop_duplicates(subset=[ID_COLUMN])
    )

    # Average all price observations for each station.
    average_prices = (
        df.groupby(ID_COLUMN, as_index=False)[PRICE_COLUMN]
        .mean()
    )

    cleaned_df = station_info.merge(
        average_prices,
        on=ID_COLUMN,
        how="left",
    )

    return cleaned_df


def main():
    print(f"Reading: {INPUT_FILE}")

    df = pd.read_csv(INPUT_FILE)

    print(f"Raw rows: {len(df):,}")

    # Validate structure.
    validate_columns(df)

    # Clean text fields.
    df = clean_text_columns(df)

    # Convert price to numeric.
    df[PRICE_COLUMN] = pd.to_numeric(
        df[PRICE_COLUMN],
        errors="coerce",
    )

    # Check invalid prices.
    invalid_prices = df[PRICE_COLUMN].isna().sum()

    if invalid_prices:
        print(
            f"WARNING: {invalid_prices:,} rows have "
            f"invalid/missing Retail Price"
        )

    # Remove rows without a station ID.
    missing_ids = df[ID_COLUMN].isna().sum()

    if missing_ids:
        print(
            f"WARNING: Removing {missing_ids:,} rows "
            f"with missing OPIS Truckstop ID"
        )

        df = df.dropna(subset=[ID_COLUMN])

    # Verify station consistency.
    validate_station_data(df)

    # Number of unique stations.
    unique_stations = df[ID_COLUMN].nunique()

    # Number of stations appearing more than once.
    records_per_station = (
        df.groupby(ID_COLUMN)
        .size()
    )

    duplicate_stations = (
        records_per_station > 1
    ).sum()

    duplicate_rows = (
        records_per_station[
            records_per_station > 1
        ].sum()
        - duplicate_stations
    )

    print("\nDataset summary")
    print("------------------------------")
    print(f"Raw records           : {len(df):,}")
    print(f"Unique stations       : {unique_stations:,}")
    print(f"Duplicate stations    : {duplicate_stations:,}")
    print(f"Duplicate rows        : {duplicate_rows:,}")

    # Aggregate duplicate records.
    cleaned_df = aggregate_prices(df)

    # Sort by OPIS ID.
    cleaned_df = cleaned_df.sort_values(
        by=ID_COLUMN
    )

    # Save.
    cleaned_df.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    print("\nCleaning complete")
    print("------------------------------")
    print(f"Output rows           : {len(cleaned_df):,}")
    print(f"Output file           : {OUTPUT_FILE}")

    # Show a few examples where aggregation happened.
    duplicate_ids = records_per_station[
        records_per_station > 1
    ].index

    if len(duplicate_ids) > 0:
        print("\nExample aggregated stations")
        print("------------------------------")

        examples = cleaned_df[
            cleaned_df[ID_COLUMN].isin(
                duplicate_ids[:5]
            )
        ]

        print(
            examples.to_string(index=False)
        )


if __name__ == "__main__":
    main()

import pandas as pd

df = pd.read_csv("fuel_prices_cleaned.csv")

print(df["Address"].value_counts().head(30))

print(df["Address"].dropna().sample(30, random_state=42).to_string(index=False))
