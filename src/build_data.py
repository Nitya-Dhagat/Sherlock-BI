from pathlib import Path

import duckdb
import pandas as pd


# Project paths
ROOT = Path(__file__).resolve().parent.parent

EXCEL_PATH = ROOT / "data" / "raw" / "online_retail_II.xlsx"
PARQUET_PATH = ROOT / "data" / "clean" / "transactions_raw.parquet"
DUCKDB_PATH = ROOT / "data" / "clean" / "whybot.duckdb"


# Standardize source column names
COLUMN_MAP = {
    "Invoice": "invoice",
    "StockCode": "stock_code",
    "Description": "description",
    "Quantity": "quantity",
    "InvoiceDate": "invoice_date",
    "Price": "unit_price",
    "Customer ID": "customer_id",
    "Country": "country",
}

REQUIRED_COLUMNS = list(COLUMN_MAP.values())


def main():
    # Check whether the Excel file exists
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(
            f"Excel dataset not found: {EXCEL_PATH}\n"
            "Place online_retail_II.xlsx inside data/raw/."
        )

    # Create output directory
    (ROOT / "data" / "clean").mkdir(
        parents=True,
        exist_ok=True,
    )

    print(f"Reading Excel file: {EXCEL_PATH.name}")

    # Read all worksheets
    sheets = pd.read_excel(
        EXCEL_PATH,
        sheet_name=None,
        engine="openpyxl",
    )

    frames = []

    for sheet_name, frame in sheets.items():
        print(f"Sheet '{sheet_name}': {len(frame):,} rows")

        # Standardize column labels
        frame.columns = [
            str(column).strip()
            for column in frame.columns
        ]

        # Rename columns
        frame = frame.rename(columns=COLUMN_MAP)

        # Validate required columns
        missing = [
            column
            for column in REQUIRED_COLUMNS
            if column not in frame.columns
        ]

        if missing:
            raise ValueError(
                f"Sheet '{sheet_name}' is missing columns: {missing}"
            )

        # Preserve the source worksheet
        frame["source_sheet"] = str(sheet_name)

        frames.append(frame)

    if not frames:
        raise ValueError("No worksheets were found in the Excel file.")

    # Combine worksheets without dropping rows
    transactions = pd.concat(
        frames,
        ignore_index=True,
    )

    # FIX 1: Invoice numbers can contain both numbers and text,
    # such as 489449 and C489449. Treat them as identifiers.
    transactions["invoice"] = transactions["invoice"].astype("string")

    # Standardize other identifier columns as strings too.
    transactions["stock_code"] = transactions["stock_code"].astype("string")
    transactions["description"] = transactions["description"].astype("string")
    transactions["country"] = transactions["country"].astype("string")

    # Parse dates while preserving unparseable values as NaT
    transactions["invoice_date"] = pd.to_datetime(
        transactions["invoice_date"],
        errors="coerce",
    )

    # Convert numeric columns
    for column in ["quantity", "unit_price", "customer_id"]:
        transactions[column] = pd.to_numeric(
            transactions[column],
            errors="coerce",
        )

    # Data quality checks before business cleaning
    exact_duplicates = int(transactions.duplicated().sum())

    missing_customers = int(
        transactions["customer_id"].isna().sum()
    )

    missing_descriptions = int(
        transactions["description"].isna().sum()
    )

    missing_dates = int(
        transactions["invoice_date"].isna().sum()
    )

    print("\n--- Combined dataset summary ---")
    print(f"Total rows: {len(transactions):,}")
    print(f"Total columns: {len(transactions.columns)}")
    print(f"Exact duplicate rows: {exact_duplicates:,}")
    print(f"Missing customer IDs: {missing_customers:,}")
    print(f"Missing descriptions: {missing_descriptions:,}")
    print(f"Missing or invalid dates: {missing_dates:,}")

    # Report date coverage
    valid_dates = transactions["invoice_date"].dropna()

    if not valid_dates.empty:
        print(f"Start date: {valid_dates.min()}")
        print(f"End date: {valid_dates.max()}")

    # Report rows from each worksheet
    print("\nRows by source sheet:")
    print(
        transactions["source_sheet"]
        .value_counts()
        .to_string()
    )

    # Report cancellation invoice count without removing rows
    cancellation_mask = (
        transactions["invoice"]
        .str.startswith("C", na=False)
    )

    print(
        "\nRows with cancellation-style invoice IDs: "
        f"{int(cancellation_mask.sum()):,}"
    )

    # Save combined raw data as Parquet
    transactions.to_parquet(
        PARQUET_PATH,
        index=False,
    )

    print(f"\nParquet saved: {PARQUET_PATH}")

    # Create or replace the DuckDB raw table
    connection = duckdb.connect(str(DUCKDB_PATH))

    try:
        parquet_path_sql = str(PARQUET_PATH).replace("'", "''")

        connection.execute(
            f"""
            CREATE OR REPLACE TABLE transactions_raw AS
            SELECT *
            FROM read_parquet('{parquet_path_sql}')
            """
        )

        row_count = connection.execute(
            "SELECT COUNT(*) FROM transactions_raw"
        ).fetchone()[0]

        print(f"DuckDB table rows: {row_count:,}")
        print(f"DuckDB database saved: {DUCKDB_PATH}")

    finally:
        connection.close()

    print("\nBuild completed successfully.")


if __name__ == "__main__":
    main()