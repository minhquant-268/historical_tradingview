# Standard library imports
import asyncio
import logging
import time
from typing import List, Optional, Dict, Any

# Third-party imports
import pandas as pd
import pyodbc
from sqlalchemy import select, update, delete, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

# Local application imports
from config.db_config import (
    AsyncSessionLocal,
    SessionLocal,
    engine,
    sync_engine,
    get_con_and_cursor,
)

# Initialize logger
logger = logging.getLogger(__name__)

# This makes the functions available when importing the module directly
__all__ = [
    "get_assets_list",
    "get_assets_list_for_data_tick",
    "get_assets_list_with_timeframe",
    "handle_olvc_historical_to_db",
    "handle_olvc_historical_to_db_session",
    "handle_tick_to_db",
    "handle_tick_to_db_session",
]


async def get_assets_list() -> List[tuple[str]]:
    """Lấy danh sách các assets từ database"""
    query = text(
        """
        SELECT
            a.provider + ':' + REPLACE(a.symbol, '&', 'AND') + ':' + CAST(a.asset_id AS NVARCHAR(10)) + ':' + CAST(p.provider_id AS NVARCHAR(10))
        FROM TradingDB.dbo.assets a
        LEFT JOIN TradingDB.dbo.providers p ON a.provider = p.provider_code AND p.platforms = 'TVC'
        WHERE a.isActive = 1 AND a.symbol IS NOT NULL AND p.provider_id IS NOT NULL
        ORDER BY a.type, a.symbol;
        """
    )

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(query)
            rows = result.fetchall()
            assets = [row[0] for row in rows if row[0]]
            logger.info(f"Fetched {len(assets)} active assets")
            if not assets:
                logger.warning("No valid assets found in database")
            return [(asset,) for asset in assets]
        except SQLAlchemyError as e:
            logger.error(f"Lỗi khi lấy danh sách assets: {str(e)}")
            return []


def get_assets_list_for_data_tick() -> List[tuple[str]]:
    """Lấy danh sách các assets từ database"""
    query = text(
        """
        SELECT symbol 
        FROM
            assets
        WHERE
            isActive = 1;
        """
    )

    with SessionLocal() as session:
        try:
            result = session.execute(query)
            rows = result.fetchall()
            return rows
        except SQLAlchemyError as e:
            logger.error(f"Lỗi khi lấy danh sách timeframe: {str(e)}")
            return []


async def get_timeframe_list() -> List[tuple[str]]:
    """Lấy danh sách các timeframe từ database"""
    query = text(
        """
        SELECT            
            t.timeframe_type,
            t.timeframe_call,
            t.seconds
        FROM
            TradingDB.dbo.timeframe t
        WHERE
            t.isActive = 1
        ORDER BY
            t.timeframe_id ASC;
        """
    )

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(query)
            rows = result.fetchall()
            return rows
        except SQLAlchemyError as e:
            logger.error(f"Lỗi khi lấy danh sách timeframe: {str(e)}")
            return []


async def get_assets_list_with_timeframe(timeframe: int) -> List[tuple[str, str]]:
    """Lấy danh sách các assets từ database với timeframe (bất đồng bộ)."""
    query = text(
        """
        DECLARE @timeframe_call VARCHAR(10) = :timeframe_call;
        SELECT 
            a.provider + ':' + REPLACE(a.symbol, '&', 'AND') + ':' + CAST(a.asset_id AS NVARCHAR(10)) + ':' + CAST(p.provider_id AS NVARCHAR(10)) + ':' + CAST(t.timeframe_id AS NVARCHAR(10)) + ':' + t.timeframe_call + ':' + t.timeframe_type
        FROM TradingDB.dbo.assets a
        LEFT JOIN TradingDB.dbo.providers p ON a.provider = p.provider_code
        INNER JOIN TradingDB.dbo.timeframe t ON t.timeframe_call = @timeframe_call
        WHERE a.isActive = 1 AND a.symbol IS NOT NULL AND p.provider_id IS NOT NULL AND t.isActive = 1
        ORDER BY a.type, a.symbol, t.timeframe_type, t.timeframe_call;
        """
    )

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(query, {"timeframe_call": str(timeframe)})
            rows = result.fetchall()
            assets = [(row[0], row[0].split(":")[-2])
                      for row in rows if row[0]]
            logger.info(
                f"Fetched {len(assets)} assets for timeframe {timeframe}")
            return assets
        except SQLAlchemyError as e:
            logger.error(f"Lỗi khi lấy danh sách assets: {str(e)}")
            return []


async def handle_olvc_historical_to_db(
    df: pd.DataFrame, symbol: str, timeframe: int
) -> bool:
    """
    Lưu dữ liệu OHLCV vào database bằng stored procedure InsertBulkTimeframeData.
    Tối ưu: Dùng pd.to_datetime với format và cache để tăng tốc parse, giảm warning.
    Thêm debug mẫu date_time, bỏ log trong loop.
    """
    if df.empty:
        logger.warning("Empty DataFrame provided")
        return False

    # Pre-validate required columns
    required_columns = [
        "symbol",
        "timeframe_type",
        "provider_code",
        "date_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns: {', '.join(missing_cols)}")
        return False

    # Debug mẫu date_time để kiểm tra định dạng
    logger.debug(
        f"Sample date_time (first 5): {df['date_time'].head(5).tolist()}")

    # Convert date_time to datetime64 with explicit format and cache
    if not pd.api.types.is_datetime64_any_dtype(df["date_time"]):
        # logger.warning("date_time column is not datetime type, converting...")
        try:
            df["date_time"] = pd.to_datetime(
                df["date_time"],
                format="%Y-%m-%d %H:%M:%S,%f",
                errors="coerce",
                cache=True,  # Cache để tăng tốc cho giá trị lặp lại
            )
            if df["date_time"].isna().any():
                invalid_count = df["date_time"].isna().sum()
                invalid_samples = (
                    df[df["date_time"].isna()]["date_time"].head(5).tolist()
                )
                logger.warning(
                    f"{invalid_count} date_time values could not be parsed for {symbol}. Samples: {invalid_samples}"
                )
        except Exception as e:
            logger.error(f"Error converting date_time for {symbol}: {str(e)}")
            return False

    # Convert DataFrame to list of tuples using itertuples()
    records = []
    num_valid = 0
    for row in df.itertuples():
        try:
            if pd.isna(row.date_time):  # Bỏ record nếu date_time là NaT
                continue

            dt_value = (
                row.date_time.to_pydatetime()
                if hasattr(row.date_time, "to_pydatetime")
                else row.date_time
            )
            record = (
                str(row.symbol),
                str(row.timeframe_type),
                str(row.provider_code),
                dt_value,
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                float(row.volume) if pd.notnull(row.volume) else 0.0,
            )
            records.append(record)
            num_valid += 1
        except (ValueError, TypeError, AttributeError):
            continue  # Không log để tăng tốc

    if not records:
        logger.warning("No valid records to insert")
        return False

    logger.info(f"Converted {num_valid}/{len(df)} valid records for {symbol}")

    try:
        conn, cursor = await get_con_and_cursor()

        # Call stored procedure with TVP
        sql = "{CALL TradingDB.dbo.InsertBulkTimeframeData (?)}"
        start_time = time.time()
        await cursor.execute(sql, (records,))
        await conn.commit()
        db_time = time.time() - start_time
        logger.info(
            f"Successfully inserted {len(records)} records for {symbol} (timeframe: {timeframe} minutes) in {db_time:.2f}s"
        )
        return True

    except Exception as e:
        logger.error(
            f"Error executing stored procedure: {str(e)}", exc_info=True)
        if conn:
            try:
                await conn.rollback()
            except Exception as rollback_err:
                logger.error(f"Rollback failed: {str(rollback_err)}")
        return False

    finally:
        if cursor:
            try:
                await cursor.close()
            except Exception as close_err:
                logger.error(f"Error closing cursor: {str(close_err)}")
        if conn:
            try:
                await conn.close()
            except Exception as close_err:
                logger.error(f"Error closing connection: {str(close_err)}")


async def handle_olvc_historical_to_db_session(
    df: pd.DataFrame, symbol: str, timeframe: int
) -> bool:
    """
    Lưu dữ liệu OHLCV vào database bằng stored procedure InsertBulkTimeframeData sử dụng AsyncSessionLocal.
    Tối ưu: Dùng pd.to_datetime với format và cache để tăng tốc parse, giảm warning.
    """
    if df.empty:
        logger.warning("Empty DataFrame provided")
        return False

    # Pre-validate required columns
    required_columns = [
        "symbol",
        "timeframe_type",
        "provider_code",
        "date_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns: {', '.join(missing_cols)}")
        return False

    # Debug mẫu date_time để kiểm tra định dạng
    logger.debug(
        f"Sample date_time (first 5): {df['date_time'].head(5).tolist()}")

    # Convert date_time to datetime64 with explicit format and cache
    if not pd.api.types.is_datetime64_any_dtype(df["date_time"]):
        # logger.warning("date_time column is not datetime type, converting...")
        try:
            df["date_time"] = pd.to_datetime(
                df["date_time"],
                format="%Y-%m-%d %H:%M:%S,%f",
                errors="coerce",
                cache=True,
            )
            if df["date_time"].isna().any():
                invalid_count = df["date_time"].isna().sum()
                invalid_samples = (
                    df[df["date_time"].isna()]["date_time"].head(5).tolist()
                )
                logger.warning(
                    f"{invalid_count} date_time values could not be parsed for {symbol}. Samples: {invalid_samples}"
                )
        except Exception as e:
            logger.error(f"Error converting date_time for {symbol}: {str(e)}")
            return False

    # Convert DataFrame to list of tuples for stored procedure
    records = []
    num_valid = 0
    for row in df.itertuples():
        try:
            if pd.isna(row.date_time):  # Bỏ record nếu date_time là NaT
                continue

            dt_value = (
                row.date_time.to_pydatetime()
                if hasattr(row.date_time, "to_pydatetime")
                else row.date_time
            )
            # Sanitize symbol and provider_code
            raw_symbol = str(row.symbol) if row.symbol is not None else ""
            raw_provider = str(
                row.provider_code) if row.provider_code is not None else ""

            sanitized_symbol = raw_symbol.replace("&", "AND")
            sanitized_provider = raw_provider

            # If symbol contains provider:symbol pattern, extract them
            if ":" in raw_symbol:
                parts = raw_symbol.split(":", 1)
                if len(parts) >= 2:
                    sanitized_provider = parts[0]
                    sanitized_symbol = parts[1].replace("&", "AND")

            # If provider equals symbol (weird case), log debug to help tracing
            if sanitized_provider == sanitized_symbol:
                logger.debug(
                    f"Provider equals symbol for row; attempting best-effort sanitize: symbol={raw_symbol}, provider={raw_provider}"
                )

            record = (
                sanitized_symbol,
                str(row.timeframe_type),
                sanitized_provider,
                dt_value,  # Python datetime
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                float(row.volume) if pd.notnull(row.volume) else 0.0,
            )
            records.append(record)
            num_valid += 1
        except (ValueError, TypeError, AttributeError):
            continue  # Không log để tăng tốc

    if not records:
        logger.warning("No valid records to insert")
        return False

    logger.info(f"Converted {num_valid}/{len(df)} valid records for {symbol}")

    async with AsyncSessionLocal() as session:
        try:
            start_time = time.time()
            # Gọi stored procedure với TVP qua raw SQL
            await session.execute(
                text("EXEC TradingDB.dbo.InsertBulkTimeframeData :records"),
                {"records": records},
            )
            await session.commit()
            db_time = time.time() - start_time
            logger.info(
                f"Successfully inserted {len(records)} records for {symbol} (timeframe: {timeframe} minutes) in {db_time:.2f}s"
            )
            return True
        except SQLAlchemyError as e:
            logger.error(
                f"Error executing stored procedure: {str(e)}", exc_info=True)
            await session.rollback()
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            await session.rollback()
            return False


async def handle_tick_to_db(df: pd.DataFrame) -> bool:
    """
    Lưu dữ liệu tick vào database bằng stored procedure InsertBulkTickData.
    Tương tự tối ưu cho tick data.
    """
    if df.empty:
        logger.warning("Empty DataFrame provided")
        return False

    # Pre-validate required columns
    required_columns = [
        "symbol",
        "time_msc",
        "bid",
        "ask",
        "last",
        "volume",
        "spread",
        "timestamp",
        "providers",
        "platforms",
        "sps",
    ]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns: {', '.join(missing_cols)}")
        return False

    # Pre-check types
    if not pd.api.types.is_datetime64_any_dtype(df["time_msc"]):
        logger.warning("time_msc column is not datetime type, converting...")
        df["time_msc"] = pd.to_datetime(df["time_msc"])

    # Convert using itertuples() - nhanh hơn iterrows()
    records = []
    num_valid = 0
    for row in df.itertuples():
        try:
            sanitized_symbol = (
                str(row.symbol).replace("&", "AND")
                if row.symbol and pd.notnull(row.symbol)
                else "Unknown"
            )
            record = (
                sanitized_symbol,
                row.time_msc.to_pydatetime(),
                float(row.bid) if pd.notnull(row.bid) else None,
                float(row.ask) if pd.notnull(row.ask) else None,
                float(row.last) if pd.notnull(row.last) else None,
                float(row.volume) if pd.notnull(row.volume) else None,
                float(row.spread) if pd.notnull(row.spread) else None,
                int(row.timestamp),
                str(row.providers),
                str(row.platforms),
                str(row.sps),
            )
            records.append(record)
            num_valid += 1
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Error processing row {row.Index}: {str(e)}")
            continue

    if not records:
        logger.warning("No valid records to insert")
        return False

    logger.debug(f"Converted {num_valid}/{len(df)} valid tick records")

    try:
        conn, cursor = await get_con_and_cursor()
        sql = "{CALL TradingDB.dbo.InsertBulkTickData (?)}"
        start_time = time.time()
        await cursor.execute(sql, (records,))
        await conn.commit()
        db_time = time.time() - start_time
        logger.info(
            f"Successfully inserted {len(records)} tick records in {db_time:.2f}s"
        )
        return True
    except Exception as e:
        logger.error(
            f"Error executing stored procedure: {str(e)}", exc_info=True)
        if conn:
            try:
                await conn.rollback()
            except Exception as rollback_err:
                logger.error(f"Rollback failed: {str(rollback_err)}")
        return False
    finally:
        if cursor:
            try:
                await cursor.close()
            except Exception as close_err:
                logger.error(f"Error closing cursor: {str(close_err)}")
        if conn:
            try:
                await conn.close()
            except Exception as close_err:
                logger.error(f"Error closing connection: {str(close_err)}")


async def handle_tick_to_db_session(df: pd.DataFrame) -> bool:
    """
    Insert tick data into TradingDB.dbo.data_ticks table using AsyncSession.

    Args:
        df: DataFrame containing tick data with required columns:
            - symbol: str
            - time_msc: datetime
            - bid: float
            - ask: float
            - last: float
            - volume: float
            - spread: float
            - timestamp: int
            - providers: str
            - platforms: str
            - sps: str

    Returns:
        bool: True if successful, False otherwise
    """
    if df.empty:
        logger.warning("Empty DataFrame provided, nothing to insert")
        return True

    required_columns = {
        "symbol",
        "time_msc",
        "bid",
        "ask",
        "last",
        "volume",
        "spread",
        "timestamp",
        "providers",
        "platforms",
        "sps",
    }

    # Check for missing columns
    missing_cols = required_columns - set(df.columns)
    if missing_cols:
        logger.error(f"Missing required columns: {', '.join(missing_cols)}")
        return False

    try:
        async with AsyncSessionLocal() as session:
            # Convert DataFrame to list of dictionaries
            records = df.to_dict("records")

            # Prepare data for bulk insert
            data_to_insert = []
            for record in records:
                data_to_insert.append(
                    {
                        "symbol": (
                            str(record["symbol"]).replace("&", "AND")
                            if pd.notnull(record["symbol"])
                            else "Unknown"
                        ),
                        "time_msc": (
                            record["time_msc"].to_pydatetime()
                            if hasattr(record["time_msc"], "to_pydatetime")
                            else record["time_msc"]
                        ),
                        "bid": (
                            float(record["bid"]) if pd.notnull(
                                record["bid"]) else None
                        ),
                        "ask": (
                            float(record["ask"]) if pd.notnull(
                                record["ask"]) else None
                        ),
                        "last": (
                            float(record["last"])
                            if pd.notnull(record["last"])
                            else None
                        ),
                        "volume": (
                            float(record["volume"])
                            if pd.notnull(record["volume"])
                            else None
                        ),
                        "spread": (
                            float(record["spread"])
                            if pd.notnull(record["spread"])
                            else None
                        ),
                        "timestamp": int(record["timestamp"]),
                        "providers": str(record["providers"]),
                        "platforms": str(record["platforms"]),
                        "sps": (
                            str(record["sps"])
                            if "sps" in record and pd.notnull(record["sps"])
                            else str(record["symbol"])
                        ),
                    }
                )

            # Execute bulk insert
            if data_to_insert:
                stmt = """
                    INSERT INTO TradingDB.dbo.data_ticks 
                    (symbol, time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps)
                    VALUES 
                    (:symbol, :time_msc, :bid, :ask, :last, :volume, :spread, :timestamp, :providers, :platforms, :sps)
                """
                await session.execute(text(stmt), data_to_insert)
                await session.commit()
                print(
                    f"✅ Successfully inserted {len(data_to_insert)} records into data_ticks"
                )
                return True

            return False

    except Exception as e:
        logger.error(
            f"Error in handle_tick_to_db_session: {str(e)}", exc_info=True)
        if "session" in locals():
            await session.rollback()
        return False


async def handle_olvc_replace_close_candle_to_db_session(
    df: pd.DataFrame, symbol: str, timeframe: int
) -> bool:
    """
    Lưu dữ liệu OHLCV vào database bằng stored procedure insert_replace_closed_candle.
    Đặc điểm: Thay thế hoàn toàn dữ liệu cũ của symbol + timeframe bằng record mới nhất.
    Tối ưu: Chỉ xử lý 1 record (mới nhất) vì procedure có REPLACE logic.
    """
    if df.empty:
        logger.warning("Empty DataFrame provided")
        return False

    # Pre-validate required columns
    required_columns = ["date_time", "open", "high", "low", "close", "volume"]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns: {', '.join(missing_cols)}")
        return False

    # Debug mẫu date_time
    logger.debug(
        f"Sample date_time (first 5): {df['date_time'].head(5).tolist()}")

    # Convert date_time to datetime64
    if not pd.api.types.is_datetime64_any_dtype(df["date_time"]):
        try:
            df["date_time"] = pd.to_datetime(
                df["date_time"],
                format="%Y-%m-%d %H:%M:%S,%f",
                errors="coerce",
                cache=True,
            )
            if df["date_time"].isna().any():
                df = df.dropna(subset=["date_time"])
                logger.warning(
                    f"Dropped {len(df)} rows with invalid date_time for {symbol}"
                )
        except Exception as e:
            logger.error(f"Error converting date_time for {symbol}: {str(e)}")
            return False

    if df.empty:
        logger.warning("No valid date_time after conversion")
        return False

    # Lấy record mới nhất (latest) để REPLACE
    # Vì procedure chỉ xử lý 1 record/lần và sẽ xóa toàn bộ cũ
    latest_record = df.loc[df["date_time"].idxmax()]

    # Validate và convert data
    try:
        record_data = {
            "p_symbol": symbol,
            "p_timeframe_call": timeframe,
            "p_date_time": latest_record["date_time"],
            "p_open": float(latest_record["open"]),
            "p_high": float(latest_record["high"]),
            "p_low": float(latest_record["low"]),
            "p_close": float(latest_record["close"]),
            "p_volume": float(
                latest_record["volume"]
            ),  # DECIMAL support (có thể = 0.0)
        }

        # Basic candle validation (optional)
        if not (
            record_data["p_high"] >= record_data["p_low"]
            and record_data["p_high"] >= record_data["p_open"]
            and record_data["p_high"] >= record_data["p_close"]
            and record_data["p_low"] <= record_data["p_open"]
            and record_data["p_low"] <= record_data["p_close"]
        ):
            logger.warning(
                f"Invalid candle logic for {symbol} at {record_data['p_date_time']}"
            )
            return False

    except (ValueError, TypeError, KeyError) as e:
        logger.error(f"Cannot process record for {symbol}: {str(e)}")
        return False

    logger.info(
        f"Processing LATEST record for {symbol} (timeframe: {timeframe}): "
        f"{record_data['p_date_time']}, volume: {record_data['p_volume']:.8f}"
    )

    async with AsyncSessionLocal() as session:
        try:
            start_time = time.time()

            # Gọi stored procedure với 1 record (REPLACE logic)
            result = await session.execute(
                text(
                    """
                    EXEC TradingDB.dbo.insert_replace_closed_candle 
                        :p_symbol, :p_timeframe_call, :p_date_time, 
                        :p_open, :p_high, :p_low, :p_close, :p_volume
                """
                ),
                record_data,
            )

            result_data = result.fetchone()
            await session.commit()

            db_time = time.time() - start_time

            if result_data and result_data.status == "SUCCESS":
                logger.info(
                    f"SUCCESS: {symbol} (timeframe: {timeframe}) - "
                    f"inserted: {result_data.inserted_rows}, "
                    f"deleted: {result_data.deleted_rows}, "
                    f"volume: {result_data.volume_used:.8f} in {db_time:.2f}s"
                )

                # Log thông tin nếu có nhiều records nhưng chỉ lưu 1
                total_records = len(df)
                if total_records > 1:
                    logger.info(
                        f"Note: {total_records - 1} older records were replaced "
                        f"by latest: {record_data['p_date_time']}"
                    )

                return True
            else:
                logger.error(
                    f"Procedure returned error for {symbol}: "
                    f"{getattr(result_data, 'error_message', 'Unknown error') if result_data else 'No result'}"
                )
                return False

        except SQLAlchemyError as e:
            logger.error(
                f"Database error for {symbol}: {str(e)}", exc_info=True)
            await session.rollback()
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error for {symbol}: {str(e)}", exc_info=True)
            await session.rollback()
            return False


async def handle_olvc_replace_close_candle_batch_to_db_session(
    df: pd.DataFrame, symbol: str, timeframe: int
) -> bool:
    """
    Lưu BATCH dữ liệu OHLCV vào database bằng direct SQL (thay thế hoàn toàn).
    Tối ưu: Xử lý nhiều records cùng lúc, hỗ trợ volume DECIMAL.
    """
    if df.empty:
        logger.warning("Empty DataFrame provided")
        return False

    # Pre-validate required columns
    required_columns = ["date_time", "open", "high", "low", "close", "volume"]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing required columns: {', '.join(missing_cols)}")
        return False

    # Convert date_time to datetime64
    if not pd.api.types.is_datetime64_any_dtype(df["date_time"]):
        try:
            df["date_time"] = pd.to_datetime(
                df["date_time"],
                format="%Y-%m-%d %H:%M:%S,%f",
                errors="coerce",
                cache=True,
            )
            if df["date_time"].isna().any():
                initial_count = len(df)
                df = df.dropna(subset=["date_time"])
                logger.warning(
                    f"Dropped {initial_count - len(df)} rows with invalid date_time for {symbol}"
                )
        except Exception as e:
            logger.error(f"Error converting date_time for {symbol}: {str(e)}")
            return False

    if df.empty:
        logger.warning("No valid records after date_time conversion")
        return False

    # Filter và clean data
    df_valid = df.dropna(subset=required_columns).copy()

    # Convert numeric columns to float (DECIMAL support)
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df_valid[col] = pd.to_numeric(df_valid[col], errors="coerce")

    # Remove invalid numeric data
    df_valid = df_valid.dropna(subset=numeric_cols)

    # Basic candle validation
    valid_mask = (
        (df_valid["high"] >= df_valid["low"])
        & (df_valid["high"] >= df_valid["open"])
        & (df_valid["high"] >= df_valid["close"])
        & (df_valid["low"] <= df_valid["open"])
        & (df_valid["low"] <= df_valid["close"])
    )

    initial_count = len(df_valid)
    df_valid = df_valid[valid_mask].copy()
    invalid_count = initial_count - len(df_valid)

    if invalid_count > 0:
        logger.warning(
            f"Filtered out {invalid_count} invalid candles for {symbol}")

    if df_valid.empty:
        logger.warning("No valid records after validation")
        return False

    # Add metadata columns
    df_valid["symbol"] = symbol
    df_valid["timeframe_call"] = timeframe

    num_records = len(df_valid)
    logger.info(
        f"Prepared {num_records} valid records for {symbol} (timeframe: {timeframe})"
    )

    async with AsyncSessionLocal() as session:
        try:
            start_time = time.time()

            # Tạo temp table với đúng schema (volume DECIMAL)
            await session.execute(
                text(
                    """
                IF OBJECT_ID('tempdb..#temp_candles') IS NOT NULL
                    DROP TABLE #temp_candles;
                
                CREATE TABLE #temp_candles (
                    symbol VARCHAR(50) NOT NULL,
                    timeframe_call INT NOT NULL,
                    date_time DATETIME2(3) NOT NULL,
                    [open] DECIMAL(10,4) NOT NULL,
                    [high] DECIMAL(10,4) NOT NULL,
                    [low] DECIMAL(10,4) NOT NULL,
                    [close] DECIMAL(10,4) NOT NULL,
                    volume DECIMAL(20,8) NOT NULL  -- DECIMAL để match table schema
                );
            """
                )
            )
            await session.commit()

            # Bulk insert vào temp table
            # Sử dụng raw SQL với VALUES cho performance
            insert_values = []
            for _, row in df_valid.iterrows():
                insert_values.append(
                    f"('{row.symbol}', {row.timeframe_call}, "
                    f"'{row.date_time}', {row.open}, {row.high}, "
                    f"{row.low}, {row.close}, {row.volume})"
                )

            insert_sql = f"""
                INSERT INTO #temp_candles (symbol, timeframe_call, date_time, [open], [high], [low], [close], volume)
                VALUES {','.join(insert_values)};
            """

            await session.execute(text(insert_sql))
            await session.commit()

            # REPLACE logic: Xóa cũ → Insert mới
            replace_sql = text(
                """
                -- Xóa dữ liệu cũ của symbol + timeframe
                DELETE FROM TradingDB.dbo.close_candles 
                WHERE symbol = :symbol AND timeframe_call = :timeframe;
                
                DECLARE @deleted_count INT = @@ROWCOUNT;
                
                -- Insert tất cả records mới từ temp table
                INSERT INTO TradingDB.dbo.close_candles (
                    symbol, timeframe_call, date_time, [open], [high], [low], [close], volume
                )
                SELECT symbol, timeframe_call, date_time, [open], [high], [low], [close], volume
                FROM #temp_candles
                ORDER BY date_time ASC;
                
                DECLARE @inserted_count INT = @@ROWCOUNT;
                
                -- Cleanup
                DROP TABLE #temp_candles;
                
                -- Return stats
                SELECT 
                    'SUCCESS' AS status,
                    @inserted_count AS inserted_rows,
                    @deleted_count AS deleted_rows,
                    :num_records AS total_processed,
                    :symbol AS symbol,
                    :timeframe AS timeframe,
                    GETDATE() AS processed_at;
            """
            )

            result = await session.execute(
                replace_sql,
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "num_records": num_records,
                },
            )

            result_data = result.fetchone()
            await session.commit()

            db_time = time.time() - start_time

            if result_data and result_data.status == "SUCCESS":
                logger.info(
                    f"BATCH SUCCESS: {symbol} (timeframe: {timeframe}) - "
                    f"inserted: {result_data.inserted_rows}, "
                    f"deleted: {result_data.deleted_rows}, "
                    f"total processed: {result_data.total_processed} in {db_time:.2f}s"
                )
                return True
            else:
                logger.error(f"Batch operation failed for {symbol}")
                return False

        except SQLAlchemyError as e:
            logger.error(
                f"Database error in batch for {symbol}: {str(e)}", exc_info=True
            )
            await session.rollback()
            return False
        except Exception as e:
            logger.error(
                f"Unexpected batch error for {symbol}: {str(e)}", exc_info=True
            )
            await session.rollback()
            return False
        finally:
            # Cleanup temp table
            try:
                await session.execute(
                    text(
                        "IF OBJECT_ID('tempdb..#temp_candles') IS NOT NULL DROP TABLE #temp_candles;"
                    )
                )
                await session.commit()
            except Exception as e:
                logger.debug(f"Temp table cleanup warning: {e}")


async def close_connections():
    """
    Đóng tất cả kết nối database đang mở.
    Hàm này nên được gọi khi ứng dụng tắt hoặc khi không cần dùng database nữa.
    """
    logger.info("Đang đóng kết nối database...")

    # Lấy engines từ db_config
    from config.db_config import sync_engine, engine, AsyncSessionLocal

    try:
        # Đóng async engine nếu tồn tại
        if "sync_engine" in globals() and sync_engine is not None:
            try:
                await sync_engine.dispose()
                logger.info("Đã đóng kết nối async engine")
            except Exception as e:
                logger.error(f"Lỗi khi đóng async engine: {e}")

        # Đóng sync engine nếu tồn tại
        if "engine" in globals() and engine is not None:
            try:
                engine.dispose()
                logger.info("Đã đóng kết nối sync engine")
            except Exception as e:
                logger.error(f"Lỗi khi đóng sync engine: {e}")

        # Đóng session factory nếu cần
        if "AsyncSessionLocal" in globals() and hasattr(AsyncSessionLocal, "close_all"):
            try:
                await AsyncSessionLocal.close_all()
                logger.info("Đã đóng tất cả session")
            except Exception as e:
                logger.error(f"Lỗi khi đóng session: {e}")

    except Exception as e:
        logger.error(f"Lỗi khi đóng kết nối database: {e}")
    finally:
        logger.info("Hoàn tất đóng kết nối database")
