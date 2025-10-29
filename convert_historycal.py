"""
convert_historycal.py

Single-file conversion of the NodeJS historical collector logic.

Requirements (install in your Python env):
  pip install sqlalchemy aioodbc websockets pandas python-dotenv pyodbc

Notes:
 - This script uses SQLAlchemy async engine with the aioodbc driver.
 - It calls the stored procedure TradingDB.dbo.InsertBulkTimeframeDataJson
   with a single NVARCHAR(MAX) JSON parameter named @json.
 - Configure DB and other settings via environment variables or a .env file.

Environment variables used:
 - DB_SERVER (host)
 - DB_PORT (optional)
 - DB_NAME (defaults to TradingDB)
 - DB_AUTH (sql | windows)
 - DB_USER / DB_PASS (for sql auth)
 - ODBC_DRIVER (optional, default 'ODBC Driver 17 for SQL Server')
 - TOKEN_CSV (path to websocket_tokens.csv)

This is a pragmatic, self-contained port of the Node/JS logic. It focuses on
the OHLCV historical flow and inserting via the JSON stored-proc.
"""

import asyncio
import json
import logging
import os
import random
import re
import ssl
import string
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus


import pandas as pd
import pytz
import websockets
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


# --- Logging -----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("convert_historycal")


# --- Load environment -------------------------------------------------------
load_dotenv()
DB_SERVER = os.getenv("DB_SERVER", "localhost")
DB_PORT = os.getenv("DB_PORT")
DB_INSTANCE = os.getenv("DB_INSTANCE")
DB_NAME = os.getenv("DB_NAME", "TradingDB")
DB_AUTH = os.getenv("DB_AUTH", "sql").lower()
DB_USER = os.getenv("DB_USER", "trading_user")
DB_PASS = os.getenv("DB_PASS", "Huy@123456")
ODBC_DRIVER = os.getenv("ODBC_DRIVER", "ODBC Driver 17 for SQL Server")
TOKEN_CSV = os.getenv("TOKEN_CSV")
MIN_CANDLES = int(os.getenv("MIN_CANDLES", "50"))


def build_odbc_connection_string():
    """Build an ODBC connection string used by aioodbc/SQLAlchemy.

    We put the whole connection string into odbc_connect and pass it to the
    async engine via the URL mssql+aioodbc:///?odbc_connect=<quoted string>
    """
    driver = ODBC_DRIVER
    server = DB_SERVER
    # If an instance name is provided, prefer server\instance form (similar to SSMS)
    if DB_INSTANCE:
        server = f"{server}\\{DB_INSTANCE}"
    elif DB_PORT:
        # server,port form
        server = f"{server},{DB_PORT}"

    if DB_AUTH == "windows":
        # Trusted connection
        parts = [f"DRIVER={{{driver}}}", f"SERVER={server}",
                 f"DATABASE={DB_NAME}", "Trusted_Connection=yes"]
    else:
        parts = [f"DRIVER={{{driver}}}", f"SERVER={server}",
                 f"DATABASE={DB_NAME}", f"UID={DB_USER}", f"PWD={DB_PASS}"]

    odbc_raw = ";".join(parts) + ";"
    return quote_plus(odbc_raw)


def build_odbc_display_string():
    """Return a masked, human-readable ODBC string for diagnostics (no password)."""
    driver = ODBC_DRIVER
    server = DB_SERVER
    if DB_INSTANCE:
        server = f"{server}\\{DB_INSTANCE}"
    elif DB_PORT:
        server = f"{server},{DB_PORT}"
    if DB_AUTH == "windows":
        return f"DRIVER={{{driver}}};SERVER={server};DATABASE={DB_NAME};Trusted_Connection=yes;"
    else:
        return f"DRIVER={{{driver}}};SERVER={server};DATABASE={DB_NAME};UID={DB_USER};PWD=*****;"


async def create_db_engine():
    """Try multiple connection variants and return the first working async engine.

    The function will try these candidate server forms in order:
      - server\instance (if DB_INSTANCE set)
      - server,port (if DB_PORT set)
      - server (host-only)

    For each candidate it builds an ODBC string and attempts a short connect (SELECT 1).
    Returns an AsyncEngine on success or raises the last exception.
    """
    candidates = []

    if DB_INSTANCE:
        candidates.append(f"{DB_SERVER}\\{DB_INSTANCE}")
    if DB_PORT:
        candidates.append(f"{DB_SERVER},{DB_PORT}")
    candidates.append(DB_SERVER)

    last_exc = None
    for server in candidates:
        driver = ODBC_DRIVER
        if DB_AUTH == "windows":
            parts = [f"DRIVER={{{driver}}}", f"SERVER={server}",
                     f"DATABASE={DB_NAME}", "Trusted_Connection=yes"]
        else:
            parts = [f"DRIVER={{{driver}}}", f"SERVER={server}",
                     f"DATABASE={DB_NAME}", f"UID={DB_USER}", f"PWD={DB_PASS}"]

        odbc_raw = ";".join(parts) + ";"
        odbc_connect = quote_plus(odbc_raw)
        url = f"mssql+aioodbc:///?odbc_connect={odbc_connect}"

        logger.info(f"Trying DB candidate server={server}")
        try:
            engine = create_async_engine(
                url, pool_pre_ping=True, pool_size=5, max_overflow=10)
            # try a quick validation query
            async with engine.connect() as conn:
                await conn.exec_driver_sql("SELECT 1")
            logger.info(f"DB candidate succeeded: {server}")
            return engine
        except Exception as e:
            logger.warning(f"DB candidate failed for {server}: {e}")
            last_exc = e
            try:
                await engine.dispose()
            except Exception:
                pass

    # none worked
    raise last_exc


async def fetch_timeframes(engine):
    q = text(
        """
        SELECT t.timeframe_type, t.timeframe_call, t.seconds
        FROM TradingDB.dbo.timeframe t
        WHERE t.isActive = 1
        ORDER BY t.timeframe_id ASC
        """
    )
    async with engine.connect() as conn:
        res = await conn.execute(q)
        rows = res.fetchall()
        return rows


async def fetch_assets(engine):
    q = text(
        """
        SELECT
            a.provider + ':' + REPLACE(a.symbol, '&', 'AND') + ':' + CAST(a.asset_id AS NVARCHAR(10)) + ':' + CAST(p.provider_id AS NVARCHAR(10)) + ':' + ISNULL(a.currency, '')
        FROM TradingDB.dbo.assets a
        LEFT JOIN TradingDB.dbo.providers p ON a.provider = p.provider_code AND p.platforms = 'TVC'
        WHERE a.isActive = 1 AND a.symbol IS NOT NULL AND p.provider_id IS NOT NULL
        ORDER BY a.type, a.symbol;
        """
    )
    async with engine.connect() as conn:
        res = await conn.execute(q)
        rows = res.fetchall()
        return [row[0] for row in rows if row[0]]


# --- TradingView websocket client (lightweight port of the JS logic) ----------
class TradingViewWebSocket:
    def __init__(self, bars=8000):
        self.ws = None
        self.connected = False
        self.bars = bars
        self.auth_token = None
        self.series_keys = {}
        self.symbol_to_chart = {}

    async def connect(self):
        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            self.ws = await websockets.connect(
                "wss://prodata.tradingview.com/socket.io/websocket",
                extra_headers={"Origin": "https://www.tradingview.com/"},
                ping_interval=None,
                max_size=None,
            )
            self.connected = True
            logger.info('✅ Kết nối WebSocket thành công')
            return True
        except Exception as e:
            logger.error(f"❌ Lỗi WebSocket: {e}")
            self.connected = False
            return False

    def store_auth_token(self, token):
        self.auth_token = token

    def _parse_messages(self, raw_data: str):
        messages = []
        pos = 0
        while pos < len(raw_data) and raw_data.startswith("~m~", pos):
            try:
                second_delim = raw_data.find("~m~", pos + 3)
                if second_delim == -1:
                    break
                length = int(raw_data[pos + 3: second_delim])
                start = second_delim + 3
                end = start + length
                if end > len(raw_data):
                    break
                message = raw_data[start:end]
                if message.startswith("~h~") or not message.strip():
                    pos = end
                    continue
                messages.append(message)
                pos = end
            except Exception:
                break
        return messages

    async def send(self, message):
        if not self.connected or self.ws is None:
            return False
        try:
            # If caller provided an already-framed TradingView string (starts with ~m~)
            # send it raw. If it's a plain string, frame it. If it's a dict/list, dump
            # to JSON and frame it the same way the JS client does.
            if isinstance(message, str):
                if message.startswith("~m~") or message.startswith("~h~"):
                    await self.ws.send(message)
                else:
                    ms = message
                    await self.ws.send(f"~m~{len(ms)}~m~{ms}")
            else:
                ms = json.dumps(message, separators=(
                    ",", ":"), ensure_ascii=False)
                await self.ws.send(f"~m~{len(ms)}~m~{ms}")
            return True
        except Exception as e:
            logger.error(f"Send error: {e}")
            self.connected = False
            return False

    async def recv(self, timeout=1.0):
        try:
            data = await asyncio.wait_for(self.ws.recv(), timeout)
            return data
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Recv error: {e}")
            self.connected = False
            return None

    async def send_auth_token(self):
        if not self.auth_token:
            return False
        return await self.send({"m": "set_auth_token", "p": [self.auth_token]})

    async def setup_symbol(self, symbol, timeframe, currency=''):
        # Create chart/session/series messages mirroring JS implementation
        chart_id = f"cs_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}"
        series_key = f"sds_{len(self.symbol_to_chart) + 1}"
        self.symbol_to_chart[f"{symbol}:{timeframe}"] = (chart_id, timeframe)
        messages = [
            {"m": "chart_create_session", "p": [chart_id, ""]},
            {"m": "switch_timezone", "p": [chart_id, "Etc/UTC"]},
            {
                "m": "resolve_symbol",
                "p": [
                    chart_id,
                    f"sds_sym_{len(self.symbol_to_chart)}",
                    # Use the currency provided (match JS behavior)
                    f"={json.dumps({'adjustment': 'splits', 'currency-id': currency, 'session': 'regular', 'symbol': symbol})}",
                ],
            },
            {
                "m": "create_series",
                "p": [chart_id, series_key, "s1", f"sds_sym_{len(self.symbol_to_chart)}", timeframe, self.bars, ""],
            },
        ]
        for m in messages:
            ok = await self.send(m)
            if not ok:
                # don't mark permanent failure here — caller should attempt reconnect/retry
                return False, None, None
            # small pacing between messages to avoid hitting server rate limits
            await asyncio.sleep(0.06)
        return True, chart_id, series_key

    def extract_heartbeat(self, raw_data: str) -> str | None:
        """Detect a heartbeat frame and return the framed payload to echo back.

        Matches JS pattern: ~m~<len>~m~~h~<Y>
        """
        if not raw_data or "~h~" not in raw_data:
            return None
        pattern = r"~m~(\d+)~m~~h~(\d+)"
        for match in re.finditer(pattern, raw_data):
            x = int(match.group(1))
            y = match.group(2)
            payload = f"~h~{y}"
            if x == len(payload):
                full_frame = f"~m~{x}~m~{payload}"
                logger.debug(
                    f"Detected valid heartbeat: {full_frame} (X={x}, Y={y})")
                return full_frame
        return None


def extract_candles_from_message(data, ws_obj, chart_id=None):
    """Extract candles and update ws_obj.series_keys when a new series key is discovered.

    Returns list of processed dicts like JS processRawData.
    """
    candles = []
    series_key = ws_obj.series_keys.get(chart_id)
    if "p" in data:
        series_data = data["p"][1] if len(data["p"]) > 1 else data["p"][0]
        if isinstance(series_data, dict):
            if series_key and series_key in series_data:
                candles = series_data[series_key].get("s", [])
            else:
                for key in series_data:
                    if key.startswith("sds_") and "s" in series_data[key]:
                        candles = series_data[key]["s"]
                        # update series key mapping for the chart
                        if chart_id and chart_id not in ws_obj.series_keys:
                            ws_obj.series_keys[chart_id] = key
                        break
        elif data.get("m") == "timescale_update":
            for item in data["p"]:
                if isinstance(item, dict):
                    if series_key and series_key in item:
                        candles = item[series_key].get("s", [])
                        break
                    for key in item:
                        if key.startswith("sds_") and "s" in item[key]:
                            candles = item[key]["s"]
                            if chart_id and chart_id not in ws_obj.series_keys:
                                ws_obj.series_keys[chart_id] = key
                            break
    processed = []
    for candle in candles:
        values = candle.get("v", []) if isinstance(candle, dict) else []
        if len(values) >= 6:
            # Timestamp: TradingView gives seconds; format to 'YYYY-MM-DD HH:mm:ss,SSS'
            ts = datetime.fromtimestamp(values[0], tz=pytz.UTC)
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
            processed.append(
                {
                    "provider:symbol": None,
                    "timeframe": None,
                    "timestamp": ts_str,
                    "open": float(values[1]),
                    "high": float(values[2]),
                    "low": float(values[3]),
                    "close": float(values[4]),
                    "volume": float(values[5]),
                }
            )
    return processed


async def call_insert_json(engine, json_text: str):
    """Call stored-proc InsertBulkTimeframeDataJson with single @json parameter."""
    async with engine.begin() as conn:
        try:
            # Use the same parameter name as the NodeJS code (records)
            await conn.execute(text("EXEC TradingDB.dbo.InsertBulkTimeframeDataJson :records"), {"records": json_text})
            logger.info("DB stored-proc executed (JSON)")
            return True
        except Exception as e:
            logger.error(f"DB stored-proc error: {e}")
            return False


async def read_latest_token_from_csv(csv_path: str):
    try:
        df = pd.read_csv(csv_path)
        if not df.empty and "token" in df.columns:
            return df["token"].iloc[-1]
    except Exception as e:
        logger.error(f"Failed to read token CSV: {e}")
    return None


async def main():
    # Create DB engine (fail gracefully if DB is unreachable)
    try:
        logger.info(
            f"DB connect string (masked): {build_odbc_display_string()}")
        engine = await create_db_engine()
    except Exception as e:
        logger.error("Cannot create DB engine or connect to the database.")
        logger.error("Detailed error: %s", str(e))
        logger.error(
            "Please verify DB_SERVER/DB_PORT/DB_AUTH/DB_USER/DB_PASS and that SQL Server is reachable from this host.")
        return

    # load token
    token = None
    if TOKEN_CSV:
        token = await read_latest_token_from_csv(TOKEN_CSV)
    else:
        # Try project default locations (match JS which looks in nodeapp/websocket_tokens.csv)
        candidate_paths = [
            Path(__file__).parent / 'nodeapp' / 'websocket_tokens.csv',
            Path(__file__).parent / 'websocket_tokens.csv',
        ]
        for p in candidate_paths:
            if p.exists():
                token = await read_latest_token_from_csv(str(p))
                if token:
                    break

    if not token:
        logger.error(
            "Auth token not found. Please supply TOKEN_CSV or place websocket_tokens.csv next to this script.")
        return

    # fetch timeframes and assets (catch DB errors and exit gracefully)
    try:
        timeframes = await fetch_timeframes(engine)
        if not timeframes:
            logger.error("No timeframes available from DB")
            return

        raw_assets = await fetch_assets(engine)
        if not raw_assets:
            logger.error("No assets available from DB")
            return
    except Exception as e:
        logger.error("Failed to query timeframes/assets from DB: %s", str(e))
        logger.error("Please verify database connectivity and credentials.")
        return

    # build TIMEFRAMES map and configured_symbols list
    TIMEFRAMES = {
        str(tf[1]): (tf[0], int(tf[1]) if str(tf[1]).isdigit() else int(tf[2]) // 60)
        for tf in timeframes
    }

    configured_symbols = []
    # raw_assets entries expected like: provider:symbol:asset_id:provider_id:currency
    for raw in raw_assets:
        parts = raw.split(":")
        if len(parts) < 2:
            continue
        base = parts[0] + ":" + parts[1]
        currency = parts[4] if len(parts) > 4 else ""
        for tf_call in TIMEFRAMES.keys():
            # store (baseSymbol, timeframe_call, currency) like JS
            configured_symbols.append((base, tf_call, currency))

    logger.info(f"Total symbol-timeframe pairs: {len(configured_symbols)}")

    ws = TradingViewWebSocket(bars=3000)
    ok = await ws.connect()
    if not ok:
        return
    ws.store_auth_token(token)
    await ws.send_auth_token()

    # Mirror JS processSetup: group symbols by timeframe and process each timeframe in order.
    # Initialize WS state similar to JS
    ws.keep_running = True
    ws.active_charts = {}
    ws.completed_symbols = set()
    ws.total_pairs = len(configured_symbols)
    ws.last_message_time = time.time()

    # build timeframe order similar to JS: numeric tf first
    all_timeframes = sorted(
        list({tf for (_, tf, _) in configured_symbols}),
        key=lambda x: int(x) if str(x).isdigit() else float('inf')
    )

    for current_tf in all_timeframes:
        if not ws.keep_running or not ws.connected:
            logger.warn(f"Connection lost at timeframe {current_tf}m")
            break

        logger.info(f"Processing timeframe {current_tf}m...")
        current_symbols = [t for t in configured_symbols if t[1] == current_tf]

        for symbol, _, currency in current_symbols:
            if not ws.connected or not ws.keep_running:
                logger.warn(f"Connection lost while processing {symbol}")
                break

            # small delay between requests (match JS 200ms)
            await asyncio.sleep(0.2)

            # attempt setup with retries
            setup_attempts = 0
            setup_success = False
            while setup_attempts < 3 and not setup_success:
                setup_attempts += 1
                try:
                    setup_success, chart_id, series_key = await ws.setup_symbol(symbol, current_tf, currency)
                    if setup_success:
                        ws.active_charts[f"{symbol}:{current_tf}"] = [
                            chart_id, series_key, current_tf]
                        logger.info(
                            f"✅ Cấu hình thành công {symbol} (Khung thời gian: {current_tf}m)")
                        break
                    else:
                        logger.error(
                            f"❌ Lỗi khi thiết lập {symbol}: (attempt {setup_attempts})")
                        # if connection dropped, try to reconnect quickly
                        if not ws.connected:
                            logger.info(
                                "Connection dropped during setup, attempting reconnect")
                            reconnected = False
                            for rtry in range(1, 6):
                                ok = await ws.connect()
                                if ok:
                                    ws.store_auth_token(token)
                                    await ws.send_auth_token()
                                    reconnected = True
                                    break
                                await asyncio.sleep(min(2 ** rtry * 0.1, 5))
                            if not reconnected:
                                logger.error("Unable to reconnect; aborting")
                                ws.keep_running = False
                                break
                        else:
                            await asyncio.sleep(0.2 * setup_attempts)
                except Exception as e:
                    logger.error(f"Error setting up {symbol}: {e}")
                    await asyncio.sleep(0.2 * setup_attempts)

            if not setup_success:
                logger.error(
                    f"❌ Thất bại thiết lập {symbol} ({current_tf}m) sau {setup_attempts} lần thử")

        # after finishing symbols for this timeframe, wait before moving to next TF
        if ws.connected:
            delay = 10 if ws.bars > 5000 else 4
            logger.info(
                f"Completed timeframe {current_tf}m, waiting {delay}s...")
            await asyncio.sleep(delay)

    # main receive loop
    try:
        while ws.connected:
            data = await ws.recv(timeout=5.0)
            if not data:
                continue
            # handle heartbeats: detect and echo back (match JS behavior)
            if "~h~" in data:
                heartbeat_msg = ws.extract_heartbeat(data)
                if heartbeat_msg:
                    logger.info(f"Heartbeat received: {heartbeat_msg}")
                    try:
                        await ws.send(heartbeat_msg)
                        logger.info(f"Echoed heartbeat: {heartbeat_msg}")
                    except Exception as e:
                        logger.error(f"Failed to echo heartbeat: {e}")

            messages = ws._parse_messages(data)
            for msg in messages:
                if not msg or not msg.strip():
                    continue
                if not (msg.startswith("{") or msg.startswith("[")):
                    continue
                try:
                    parsed = json.loads(msg)
                except Exception:
                    continue

                mtype = parsed.get("m", "")
                chart_id = parsed.get("p", [None])[0]
                # find symbol,timeframe from ws.active_charts first (matches JS activeCharts),
                # fallback to ws.symbol_to_chart
                symbol_found, timeframe_found = (None, None)
                # ws.active_charts keys are formatted as "{symbol}:{tf}"
                for key, val in getattr(ws, 'active_charts', {}).items():
                    try:
                        c_id = val[0]
                    except Exception:
                        continue
                    if c_id == chart_id:
                        # key looks like 'PROVIDER:SYMBOL:tf' or 'PROVIDER:SYMBOL:tf'
                        parts = key.split(":")
                        # reconstruct base provider:symbol
                        if len(parts) >= 2:
                            symbol_found = parts[0] + ":" + parts[1]
                            # timeframe stored as last element in active_charts value
                            timeframe_found = val[2] if len(val) > 2 else None
                        break
                if symbol_found is None:
                    symbol_info = next(
                        ((s, t) for s, (c, t) in ws.symbol_to_chart.items() if c == chart_id), (None, None))
                    symbol_found, timeframe_found = symbol_info

                if mtype == "timescale_update" and chart_id:
                    # extract candles and update ws.series_keys when necessary
                    processed = extract_candles_from_message(
                        parsed, ws, chart_id)
                    if not processed:
                        continue

                    # convert to DataFrame and set metadata
                    df = pd.DataFrame(processed)
                    if df.empty:
                        continue

                    # Only proceed when the data has enough candles (match JS check >50)
                    if len(df) <= MIN_CANDLES:
                        logger.debug(
                            f"Not enough candles ({len(df)}) for {symbol_found} {timeframe_found}m, need >{MIN_CANDLES}")
                        continue

                    # set provider/symbol/timeframe fields using the configured_symbols mapping
                    raw_match = next((r for (
                        s, t, r) in configured_symbols if s == symbol_found and t == timeframe_found), None)
                    provider_code = None
                    if raw_match:
                        # In configured_symbols we stored currency as the third element (r)
                        # Need to find corresponding raw_assets entry to retrieve provider/symbol/provider_id/asset_id
                        # Search raw_assets for an entry that starts with base provider:symbol
                        base_symbol = symbol_found
                        matching_raw_full = next(
                            (ra for ra in raw_assets if ra.startswith(base_symbol)), None)
                        if matching_raw_full:
                            parts = matching_raw_full.split(":")
                            if len(parts) >= 4:
                                provider_code = parts[0]
                                symbol_name = parts[1].replace("&", "AND")
                                asset_id = parts[2]
                                provider_id = parts[3]
                                # JS uses symbol name (without provider prefix) in the 'symbol' column
                                df["symbol"] = symbol_name
                                df["asset_id"] = asset_id
                                df["provider_id"] = provider_id
                                df["timeframe"] = timeframe_found
                            else:
                                logger.debug(
                                    f"Invalid raw asset format: {matching_raw_full}")
                                continue
                        else:
                            logger.debug(
                                f"No matching raw asset found for base {base_symbol}")
                            continue
                    else:
                        df["symbol"] = symbol_found
                        df["asset_id"] = "0"
                        df["provider_id"] = "0"
                        df["timeframe"] = timeframe_found

                    df["timeframe_type"] = TIMEFRAMES.get(
                        timeframe_found, ("M1", 1))[0]
                    df["provider_code"] = provider_code or "TVC"

                    # convert TradingView timestamp string to ISO UTC as Node does
                    def to_iso(ts_str):
                        try:
                            dt = datetime.strptime(
                                ts_str, "%Y-%m-%d %H:%M:%S,%f")
                            # ensure tz-aware UTC
                            dt = dt.replace(tzinfo=pytz.UTC)
                            return dt.isoformat()
                        except Exception:
                            return ts_str

                    df["date_time"] = df["timestamp"].apply(to_iso)
                    columns_order = ["symbol", "timeframe_type", "provider_code",
                                     "date_time", "open", "high", "low", "close", "volume"]
                    df = df[columns_order]

                    # drop last candle (incomplete) if more than 1 row
                    if len(df) > 1:
                        df = df.iloc[:-1]

                    if len(df) < 1:
                        continue

                    records = [r for r in df.to_dict("records")]
                    json_payload = json.dumps(
                        {"records": records}, default=str, ensure_ascii=False)

                    # Log similar to JS: show HISTORICAL line and last 3 rows
                    logger.info(
                        f"[HISTORICAL] OHLCV Data - {symbol_found} (Timeframe: {timeframe_found}m) - {len(records)} records")
                    try:
                        last_three = records[-3:]
                        for row in last_three:
                            logger.info(
                                f"{row.get('symbol')} {row.get('timeframe_type')} {row.get('date_time')} O:{row.get('open')} H:{row.get('high')} L:{row.get('low')} C:{row.get('close')} V:{row.get('volume')}")
                    except Exception:
                        pass

                    start_db = time.time()
                    ok = await call_insert_json(engine, json_payload)
                    elapsed = time.time() - start_db
                    if ok:
                        logger.info(
                            f"✅ Successfully inserted {len(records)} records for {symbol_found} (timeframe: {timeframe_found}m) in {elapsed:.2f}s")
                    else:
                        logger.error(
                            f"❌ Database insert failed for {symbol_found} ({timeframe_found}m)")

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        try:
            if ws and ws.ws:
                await ws.ws.close()
        except Exception:
            pass
        try:
            await engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
