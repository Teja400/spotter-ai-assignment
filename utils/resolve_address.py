"""
Resolve fuel-stop addresses against the Geoapify geocoding API.

For every row in the input CSV, the truckstop name/city/state is sent to
Geoapify's geocode search endpoint. The API returns a *list* of candidate
matches (not a single result), so we need a strategy to pick the right one.
See select_best_candidate() for the full selection priority - in short:
prefer a confident/fuel-station match in the expected state, and if no
usable same-state business match exists, fall back to the city-level
(administrative) coordinates for that same city/state rather than ever
"jumping" to a same-named business in a different city or state. If not
even a city-level match is available, the row is left unresolved.

Two CSVs are produced:
  - OUTPUT_FILE: only the "critical" columns (OPIS Truckstop ID, Truckstop
    Name, Address, City, State, Rack ID, Retail Price, Latitude,
    Longitude). City/State here are the *corrected* values whenever the
    geocoder disagreed with the source data.
  - DETAILS_FILE: everything else useful for auditing the match (original
    City/State, mismatch flags, formatted address, match category/
    confidence/method) keyed by OPIS Truckstop ID so it can be joined back
    to OUTPUT_FILE if needed.

Both files are written incrementally, one row at a time, and re-running the
script will skip any OPIS Truckstop ID already present in DETAILS_FILE. This
matters because Geoapify's free tier has a *daily* request quota - if the
run is interrupted (network issue, quota exhausted, process killed), no
progress or API credits already spent are lost; simply re-run the script
(the next day, if the quota was the cause) and it will resume where it left
off.
"""

import os
import re
import time

import pandas as pd
import requests

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("API_KEY")
GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"

INPUT_FILE = "fuel_prices_cleaned.csv"
OUTPUT_FILE = "fuel_prices_resolved.csv"
DETAILS_FILE = "fuel_prices_resolved_details.csv"

ID_COLUMN = "OPIS Truckstop ID"

# The only columns that belong in the "clean" output CSV.
OUTPUT_COLUMNS = [
    "OPIS Truckstop ID",
    "Truckstop Name",
    "Address",
    "City",
    "State",
    "Rack ID",
    "Retail Price",
    "Latitude",
    "Longitude",
]

# A candidate is trusted outright if Geoapify's own confidence score meets
# this threshold (rank.confidence ranges from 0.0 to 1.0).
CONFIDENCE_THRESHOLD = 0.60

# We only want fuel stations - this is the category Geoapify assigns to
# gas/fuel stations (see result.json for a real example).
FUEL_CATEGORY = "service.vehicle.fuel"

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SECONDS = 0.15  # be polite to the free-tier API rate limit
MAX_RETRIES = 3

# Status codes that mean the API key/plan itself is the problem (invalid
# key, quota/credits exhausted, payment required). Retrying these wastes
# time and doesn't help - the whole run should stop immediately so we don't
# burn through the remaining rows generating nothing but failures, and so
# whatever has already been resolved is preserved.
QUOTA_OR_AUTH_STATUS_CODES = {401, 402, 403}

# Match methods that indicate a weak/uncertain result, worth retrying with
# a store-number-stripped name (see strip_store_number()).
WEAK_MATCH_METHODS = {
    "low_confidence_fallback",
    "city_level_fallback",
    "state_level_fallback",
    "no_match_wrong_state_only",
    "no_match",
}

# Store numbers (e.g. "CEFCO #2089", "STRIPES 7FLEET #42472", "ALLSUPS #2437")
# are almost never part of the business's real-world/OSM name, and including
# them can cause the free-text search to miss an otherwise good match.
STORE_NUMBER_PATTERN = re.compile(r"#\s*\d+|\b\d{3,}\b")

# Only the states we expect to see in this dataset, but any US state
# abbreviation can be added here without changing the rest of the script.
STATE_ABBR_TO_NAME = {
    "TX": "Texas",
    "NM": "New Mexico",
    "OK": "Oklahoma",
}


class QuotaExceededError(Exception):
    """Raised when the geocoding API reports an auth/quota/billing problem."""


def state_full_name(state_abbr: str) -> str:
    """Return the full state name for a 2-letter abbreviation, if known."""
    return STATE_ABBR_TO_NAME.get(str(state_abbr).strip().upper(), state_abbr)


def strip_store_number(name: str) -> str:
    """
    Remove trailing store/unit numbers from a business name, e.g.:
        "CEFCO #2089"                -> "CEFCO"
        "STRIPES 7FLEET #42472"      -> "STRIPES 7FLEET"
        "ALLSUPS CONVENIENCE #2420"  -> "ALLSUPS CONVENIENCE"

    Returns the cleaned name, or the original (stripped of whitespace) if
    nothing was removed / the result would be empty.
    """
    cleaned = STORE_NUMBER_PATTERN.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" #-")

    return cleaned or name.strip()


def fetch_candidates(name: str, city: str, state: str) -> list:
    """
    Query Geoapify for geocode candidates matching the given name/city/state.

    Returns the list of candidate dicts (may be empty). Raises
    QuotaExceededError if the API reports an auth/quota/billing problem, so
    the caller can stop the whole run rather than burning through every
    remaining row with guaranteed failures.
    """
    params = {
        "name": name,
        "city": city,
        "state": state_full_name(state),
        "country": "United States of America",
        "format": "json",
        "apiKey": API_KEY,
    }

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                GEOCODE_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in QUOTA_OR_AUTH_STATUS_CODES:
                raise QuotaExceededError(
                    f"Geoapify returned HTTP {response.status_code} - "
                    f"likely an invalid API key or exhausted quota: {response.text[:200]}"
                )

            if response.status_code == 429:
                # Rate limited - back off and retry.
                time.sleep(1.0 * attempt)
                continue

            if 400 <= response.status_code < 500:
                # A permanent client-side error (e.g. malformed request) -
                # retrying won't help, so don't waste further attempts.
                print(f"WARNING: HTTP {response.status_code} for '{name}, {city}, {state}' - skipping retries.")
                return []

            response.raise_for_status()
            payload = response.json()
            return payload.get("results", [])

        except QuotaExceededError:
            raise

        except requests.RequestException as exc:
            last_error = exc
            time.sleep(0.5 * attempt)

    print(f"WARNING: geocode request failed for '{name}, {city}, {state}': {last_error}")
    return []


ADMINISTRATIVE_CATEGORY = "administrative"


def select_best_candidate(candidates: list, expected_state: str, expected_city: str):
    """
    Choose the candidate to trust from the list returned by Geoapify.

    Priority order (never jump to a different city/state than expected):
        1. If the API returned exactly one candidate overall, use it as-is -
           there is nothing else to choose from.
        2. Among non-administrative candidates in the *expected state*:
           - a high-confidence top result is trusted directly.
           - otherwise prefer an actual fuel station (category
             "service.vehicle.fuel").
           - otherwise fall back to the top same-state result anyway
             (still same state, just lower confidence).
        3. If there are no usable same-state business candidates at all,
           we do NOT jump to a same-named business in a different state or
           city (that has proven unreliable - e.g. a "Pilot Travel Centers"
           hundreds of miles away). Instead we fall back to the
           administrative (city-level) coordinates for the expected
           city/state if the API returned one, so at least the location
           stays anchored to the correct place.
        4. If nothing in the expected state is available at all (not even
           a city-level match), the row is left unresolved rather than
           guessing a wrong state/city.

    Returns a tuple (candidate_dict_or_None, match_method_str).
    """
    if not candidates:
        return None, "no_match"

    if len(candidates) == 1:
        return candidates[0], "single_candidate"

    expected_state = expected_state.strip().upper()
    expected_city_norm = expected_city.strip().upper()

    non_admin_candidates = [
        c for c in candidates if c.get("category") != ADMINISTRATIVE_CATEGORY
    ]

    same_state_non_admin = [
        c for c in non_admin_candidates if (c.get("state_code") or "").strip().upper() == expected_state
    ]

    if same_state_non_admin:
        top = same_state_non_admin[0]
        top_confidence = top.get("rank", {}).get("confidence", 0) or 0

        if top_confidence >= CONFIDENCE_THRESHOLD:
            return top, "high_confidence_top_result"

        for candidate in same_state_non_admin:
            if candidate.get("category") == FUEL_CATEGORY:
                return candidate, "fuel_category_match"

        return same_state_non_admin[0], "low_confidence_fallback"

    # No usable same-state business candidate - avoid jumping to a
    # different city/state. Fall back to the administrative (city-level)
    # match for the expected city/state, if the API returned one.
    same_state_admin = [
        c for c in candidates
        if c.get("category") == ADMINISTRATIVE_CATEGORY
        and (c.get("state_code") or "").strip().upper() == expected_state
    ]

    same_city_state_admin = [
        c for c in same_state_admin if (c.get("city") or "").strip().upper() == expected_city_norm
    ]

    if same_city_state_admin:
        return same_city_state_admin[0], "city_level_fallback"

    if same_state_admin:
        return same_state_admin[0], "state_level_fallback"

    # Nothing at all in the expected state - do not guess a wrong
    # state/city, leave the row unresolved instead.
    return None, "no_match_wrong_state_only"


def resolve_row(row: pd.Series) -> dict:
    """Geocode a single CSV row and build the enriched output fields."""
    name = str(row["Truckstop Name"])
    city = str(row["City"])
    state = str(row["State"])

    candidates = fetch_candidates(name, city, state)
    best, match_method = select_best_candidate(candidates, state, city)

    # If the name-as-is produced a weak/uncertain result, retry with the
    # store/unit number stripped out (e.g. "CEFCO #2089" -> "CEFCO") - the
    # free-text search can match better without it, and this often lands a
    # confident, same-state business match instead of a fallback.
    stripped_name = strip_store_number(name)

    if match_method in WEAK_MATCH_METHODS and stripped_name.upper() != name.strip().upper():
        retry_candidates = fetch_candidates(stripped_name, city, state)
        retry_best, retry_method = select_best_candidate(retry_candidates, state, city)

        retry_is_better = (
            retry_best is not None
            and retry_method not in WEAK_MATCH_METHODS
        )

        if retry_is_better:
            best, match_method = retry_best, f"{retry_method}_stripped_name"

    if best is None:
        return {
            "Latitude": None,
            "Longitude": None,
            "Resolved State": state,
            "State Mismatch": False,
            "Resolved City": city,
            "City Mismatch": False,
            "Formatted Address": None,
            "Match Category": None,
            "Match Confidence": None,
            "Match Method": match_method,
        }

    resolved_state = best.get("state_code") or state
    resolved_city = best.get("city") or city

    state_mismatch = resolved_state.strip().upper() != state.strip().upper()
    city_mismatch = resolved_city.strip().upper() != city.strip().upper()

    return {
        "Latitude": best.get("lat"),
        "Longitude": best.get("lon"),
        "Resolved State": resolved_state,
        "State Mismatch": state_mismatch,
        "Resolved City": resolved_city,
        "City Mismatch": city_mismatch,
        "Formatted Address": best.get("formatted"),
        "Match Category": best.get("category"),
        "Match Confidence": best.get("rank", {}).get("confidence"),
        "Match Method": match_method,
    }


def _append_row(file_path: str, row_dict: dict, columns: list, max_attempts: int = 6) -> None:
    """Append a single row to a CSV, writing the header only if the file is new.

    Retries with backoff on PermissionError, which on Windows is often caused
    by a transient lock from OneDrive sync, antivirus scanning, or another
    process briefly holding the file open rather than a real permissions issue.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            file_exists = os.path.exists(file_path)
            pd.DataFrame([row_dict], columns=columns).to_csv(
                file_path,
                mode="a",
                header=not file_exists,
                index=False,
            )
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(min(2 ** attempt * 0.5, 15))
    raise last_error


def _load_processed_ids(details_file: str) -> set:
    """Return the set of OPIS Truckstop IDs already present in DETAILS_FILE."""
    if not os.path.exists(details_file):
        return set()

    existing = pd.read_csv(details_file, usecols=[ID_COLUMN])
    return set(existing[ID_COLUMN].tolist())


def _check_output_schema(file_path: str, expected_columns: list) -> None:
    """
    Guard against silently corrupting an existing output file.

    Rows are appended incrementally (see _append_row()), so if OUTPUT_FILE
    already exists from an earlier/older run with a *different* set of
    columns (e.g. the previous version of this script, which wrote extra
    diagnostic columns directly into it), blindly appending would misalign
    every column. Fail loudly instead and tell the user what to do.
    """
    if not os.path.exists(file_path):
        return

    existing_columns = pd.read_csv(file_path, nrows=0).columns.tolist()

    if existing_columns != expected_columns:
        raise RuntimeError(
            f"'{file_path}' already exists with a different set of columns "
            f"than this version of the script produces.\n"
            f"  Existing columns : {existing_columns}\n"
            f"  Expected columns : {expected_columns}\n"
            f"Move/rename/delete the existing file (you mentioned you already "
            f"have a backup) before re-running, so new rows aren't appended "
            f"under mismatched headers."
        )


def main():
    if not API_KEY:
        raise RuntimeError("API_KEY is not set. Add it to your .env file.")

    df = pd.read_csv(INPUT_FILE)

    required_columns = {"Truckstop Name", "City", "State", ID_COLUMN}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # Fail fast if OUTPUT_FILE exists from an incompatible earlier run,
    # rather than corrupting it with misaligned appended rows.
    _check_output_schema(OUTPUT_FILE, OUTPUT_COLUMNS)

    total = len(df)

    # Resume support: skip any row already resolved in a previous run (this
    # matters because the free-tier API has a *daily* request quota - if a
    # run is interrupted, re-running should not re-spend credits on rows
    # that are already done).
    processed_ids = _load_processed_ids(DETAILS_FILE)
    remaining_df = df[~df[ID_COLUMN].isin(processed_ids)]

    if processed_ids:
        print(f"Resuming: {len(processed_ids)}/{total} rows already resolved, {len(remaining_df)} remaining.")

    stopped_early = False

    for i, row in remaining_df.iterrows():
        print(f"[{i + 1}/{total}] Resolving: {row['Truckstop Name']} ({row['City']}, {row['State']})")

        try:
            result = resolve_row(row)
        except QuotaExceededError as exc:
            print(f"\nSTOPPING: {exc}")
            print(f"Progress is saved - {len(processed_ids)} rows resolved so far. Re-run this script later to resume.")
            stopped_early = True
            break

        output_row = {
            "OPIS Truckstop ID": row[ID_COLUMN],
            "Truckstop Name": row["Truckstop Name"],
            "Address": row.get("Address"),
            "City": result["Resolved City"],
            "State": result["Resolved State"],
            "Rack ID": row.get("Rack ID"),
            "Retail Price": row.get("Retail Price"),
            "Latitude": result["Latitude"],
            "Longitude": result["Longitude"],
        }

        details_row = {
            "OPIS Truckstop ID": row[ID_COLUMN],
            "Truckstop Name": row["Truckstop Name"],
            "Original City": row["City"],
            "Original State": row["State"],
            "Resolved City": result["Resolved City"],
            "Resolved State": result["Resolved State"],
            "City Mismatch": result["City Mismatch"],
            "State Mismatch": result["State Mismatch"],
            "Formatted Address": result["Formatted Address"],
            "Match Category": result["Match Category"],
            "Match Confidence": result["Match Confidence"],
            "Match Method": result["Match Method"],
        }

        _append_row(OUTPUT_FILE, output_row, OUTPUT_COLUMNS)
        _append_row(DETAILS_FILE, details_row, list(details_row.keys()))

        processed_ids.add(row[ID_COLUMN])

        time.sleep(REQUEST_DELAY_SECONDS)

    # Summary - read back the full details file so the counts cover every
    # row resolved across this run and any prior (resumed) runs.
    details_df = pd.read_csv(DETAILS_FILE) if os.path.exists(DETAILS_FILE) else pd.DataFrame()

    resolved_count = details_df["Resolved State"].notna().sum() if not details_df.empty else 0
    mismatch_count = details_df["State Mismatch"].sum() if not details_df.empty else 0

    print("\nGeocoding summary")
    print("=" * 40)
    print(f"Total rows in input   : {total}")
    print(f"Rows resolved so far  : {len(details_df)}")
    print(f"With coordinates      : {resolved_count}")
    print(f"State mismatches      : {mismatch_count}")

    if not details_df.empty:
        print("Match method counts   :")
        print(details_df["Match Method"].value_counts().to_string())

    if stopped_early:
        print(f"\nRun stopped early. Saved so far -> {OUTPUT_FILE}, {DETAILS_FILE}")
    else:
        print(f"\nAll rows processed. Saved -> {OUTPUT_FILE}, {DETAILS_FILE}")


if __name__ == "__main__":
    main()
