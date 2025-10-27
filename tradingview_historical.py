from queue import Queue
import asyncio
import ctypes
import pytz
import logging
import ssl
import threading
import string
import random
import time
from datetime import datetime, timezone
import pandas as pd
import json
import websockets
import numpy as np
import os
import sys
import base64
from dotenv import load_dotenv, find_dotenv
from pathlib import Path
import re

# Add project root to Python path
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Get the correct base directory for config files
if getattr(sys, "frozen", False):
    # When running as EXE, always use the directory containing the EXE
    base_dir = Path(sys.executable).parent
    historical_config_path = base_dir / "historical_config.json"

    # Create default config if it doesn't exist
    if not historical_config_path.exists():
        with open(historical_config_path, "w", encoding="utf-8") as f:
            json.dump({"last_time": ""}, f, indent=4)
else:
    # Running as Python script
    historical_config_path = Path(__file__).parent / "historical_config.json"

# Load environment variables
load_dotenv()
env_path = find_dotenv()
print(f"Đang đọc file .env từ: {env_path}")

# Now import services
from services import trading_service

# Hàng đợi toàn cục
data_queue = Queue()

MAX_RETRIES = 1
RETRY_DELAY = 1

historical_config = None

# Get the directory where the executable is located
if getattr(sys, "frozen", False):
    # If running as compiled executable
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # If running as script
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_historical_config():
    global historical_config
    if historical_config is None:
        try:
            with open(historical_config_path, "r", encoding="utf-8") as f:
                historical_config = json.load(f)
                logger.info("✅ Successfully loaded historical config")
        except Exception as e:
            logger.error(f"❌ Failed to load historical config: {e}")
            historical_config = None
    return historical_config


def set_console_title(title):
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.SetConsoleTitleW(title)
    except Exception as e:
        print(f"Không thể đặt tiêu đề cửa sổ: {e}")


def cleanup_old_logs(log_file_path, max_lines=100000, max_retries=3):
    # Lưu lại các handler hiện tại
    old_handlers = logging.root.handlers[:]

    # Tạo một logger tạm thời để ghi log trong quá trình cleanup
    temp_logger = logging.getLogger("temp_logger")
    temp_logger.setLevel(logging.INFO)

    # Xóa các handler cũ nếu có
    for handler in temp_logger.handlers[:]:
        temp_logger.removeHandler(handler)

    # Sử dụng console handler tạm thời
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    temp_logger.addHandler(console_handler)

    def try_remove_file(file_path):
        """Thử xóa file với cơ chế retry"""
        for attempt in range(max_retries):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                return True
            except PermissionError:
                if attempt == max_retries - 1:
                    return False
                time.sleep(0.5)  # Chờ một chút trước khi thử lại
        return False

    try:
        if not os.path.exists(log_file_path):
            temp_logger.debug(
                f"Log file not found, nothing to clean up: {log_file_path}"
            )
            return False

        # Kiểm tra kích thước file
        try:
            file_size = os.path.getsize(log_file_path)
        except OSError as e:
            temp_logger.error(f"Error getting file size for {log_file_path}: {e}")
            return False
        if file_size == 0:
            temp_logger.info("Log file is empty, nothing to clean up")
            return True

        temp_file = log_file_path + ".tmp"

        # Nếu file nhỏ hơn 1MB hoặc ít hơn max_lines dòng, không cần xử lý
        try:
            with open(log_file_path, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)

            if line_count <= max_lines:
                temp_logger.debug(
                    f"Log file has {line_count} lines (≤ {max_lines}), no cleanup needed"
                )
                return True

            temp_logger.info(f"Log file has {line_count} lines, cleaning up...")
        except Exception as e:
            temp_logger.error(f"Error counting lines in {log_file_path}: {e}")
            return False

        # Đọc tất cả các dòng vào bộ nhớ
        try:
            with open(log_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                total_lines = len(lines)

            if total_lines <= max_lines:
                temp_logger.info(
                    f"Log file has {total_lines} lines (≤ {max_lines}), no cleanup needed"
                )
                return True

            # Tạo thư mục nếu chưa tồn tại
            os.makedirs(os.path.dirname(temp_file), exist_ok=True)

            # Ghi các dòng cuối cùng vào file tạm
            try:
                with open(temp_file, "w", encoding="utf-8") as f_out:
                    f_out.writelines(lines[-max_lines:])
                    lines_written = min(max_lines, len(lines))

                # Kiểm tra xem file tạm đã được tạo chưa
                if not os.path.exists(temp_file):
                    temp_logger.error(f"Không thể tạo file tạm: {temp_file}")
                    return False

                # Đóng tất cả các file handler cũ
                for handler in logging.root.handlers[:]:
                    if isinstance(handler, logging.FileHandler):
                        handler.close()

                # Chờ một chút để đảm bảo file được đóng hoàn toàn
                time.sleep(0.5)

                # Thay thế file gốc bằng file tạm
                for attempt in range(max_retries):
                    try:
                        # Thử xóa file đích nếu tồn tại
                        if not try_remove_file(log_file_path):
                            temp_logger.warning(
                                f"Không thể xóa file log cũ (lần thử {attempt + 1}/{max_retries}). "
                                "Có thể đang bị khóa bởi tiến trình khác."
                            )
                            if attempt == max_retries - 1:
                                raise PermissionError("Đạt số lần thử tối đa")
                            time.sleep(1)
                            continue

                        # Đổi tên file tạm thành file chính
                        os.rename(temp_file, log_file_path)
                        temp_logger.info(
                            f"Đã cắt gọn file log thành công. Giữ lại {lines_written} dòng gần nhất."
                        )
                        return True

                    except (PermissionError, OSError) as e:
                        if attempt == max_retries - 1:  # Lần thử cuối cùng
                            temp_logger.error(
                                f"Không thể ghi đè file log sau {max_retries} lần thử. "
                                f"Lỗi: {str(e)}. File log sẽ được giữ nguyên."
                            )
                            return False
                        time.sleep(1)  # Chờ 1 giây trước khi thử lại

            except Exception as e:
                temp_logger.error(f"Lỗi khi ghi file tạm: {e}")
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                return False

        except PermissionError as e:
            temp_logger.error(
                f"Không thể truy cập file log (có thể đang bị khóa bởi tiến trình khác): {e}"
            )
            return False

        except Exception as e:
            temp_logger.error(f"Lỗi khi xử lý file log: {e}", exc_info=True)
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
            return False

    except Exception as e:
        temp_logger.error(f"Lỗi không xác định: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
        return False

    finally:
        try:
            # Dọn dẹp logger tạm
            for handler in temp_logger.handlers[:]:
                handler.close()
                temp_logger.removeHandler(handler)

            # Khôi phục lại các handler cũ
            logging.root.handlers = old_handlers

            # Kiểm tra nếu temp_file tồn tại và xóa nếu cần
            if "temp_file" in locals() and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logging.error(f"Không thể xóa file tạm {temp_file}: {e}")
        except Exception as e:
            logging.error(f"Lỗi trong quá trình dọn dẹp: {e}")
            # Cố gắng khôi phục logging nếu có lỗi
            logging.root.handlers = old_handlers


NAME_LOG_FILE = "tradingview_historical.log"


# Cấu hình logging
class UnicodeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        UnicodeStreamHandler(),
        logging.FileHandler(NAME_LOG_FILE, encoding="utf-8"),
    ],
    encoding="utf-8",
)
logger = logging.getLogger(__name__)

# Cấu hình pandas và numpy
pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
np.set_printoptions(threshold=sys.maxsize, linewidth=1000)

# Cấu hình console cho Windows
if sys.stdout.isatty() and sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.GetStdHandle(-11)
        mode = wintypes.DWORD()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        mode.value |= 0x0004
        kernel32.SetConsoleMode(handle, mode)
    except Exception as e:
        logger.warning(f"Không thể cài đặt chế độ console: {e}")


class TradingViewWebSocket:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.keep_running = True
        self.ping_interval = 10
        self.ping_timeout = 15
        self.last_message_time = time.time()
        self.bars = 8000
        self.auth_token = None
        self.auth_cookies = None
        self.processing_lock = asyncio.Lock()
        self.configured_symbols = []
        self.symbol_to_chart = {}
        self.series_keys = {}
        self.quote_to_symbol = {}
        self.active_charts = {}
        self.saved_data = {}  # Track saved data status
        self.completed_symbols = set()  # Track completed (symbol, timeframe) pairs
        self.total_symbols = 0  # Will be set when symbols are loaded

    async def connect(self):
        try:
            if self.ws:
                await self.ws.close()
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            self.ws = await websockets.connect(
                f"wss://prodata.tradingview.com/socket.io/websocket",
                extra_headers={
                    "Origin": "https://www.tradingview.com/",
                },
                ping_interval=None,
                ping_timeout=0.1,
                max_size=None,
            )
            self.connected = True
            self.last_message_time = time.time()
            logger.info("Kết nối WebSocket thành công")
            return True
        except Exception as e:
            logger.error(f"Lỗi kết nối: {e}")
            self.connected = False
            return False

    def store_auth_token(self, token):
        self.auth_token = token

    def store_symbols(self, symbols):
        self.configured_symbols = symbols
        self.total_symbols = len(symbols)
        logger.info(f"Đã lưu {self.total_symbols} cặp symbol-timeframe")

    def _parse_messages(self, raw_data: str):
        messages = []
        pos = 0
        while pos < len(raw_data) and raw_data.startswith("~m~", pos):
            try:
                second_delim = raw_data.find("~m~", pos + 3)
                if second_delim == -1:
                    break
                length = int(raw_data[pos + 3 : second_delim])
                start = second_delim + 3
                end = start + length
                if end > len(raw_data):
                    break
                message = raw_data[start:end]
                # Bỏ qua heartbeat và message rỗng
                if message.startswith("~h~") or not message.strip():
                    pos = end
                    continue
                messages.append(message)
                pos = end
            except (ValueError, IndexError):
                break
        return messages

    def extract_heartbeat(self, raw_data: str) -> str | None:
        if not raw_data or "~h~" not in raw_data:
            return None

        pattern = r"~m~(\d+)~m~~h~(\d+)"
        matches = re.finditer(pattern, raw_data)

        for match in matches:
            x = int(match.group(1))  # LEN
            y = match.group(2)  # ~h~Y (Y là str, ví dụ '6')
            payload = f"~h~{y}"
            actual_len = len(payload)

            # Xác thực: LEN phải khớp độ dài PAYLOAD
            if x == actual_len:
                full_frame = f"~m~{x}~m~{payload}"
                logger.debug(f"Detected valid heartbeat: {full_frame} (X={x}, Y={y})")
                return full_frame
            else:
                logger.debug(
                    f"Invalid heartbeat detected (LEN mismatch): X={x}, actual={actual_len}, Y={y}"
                )

        logger.debug("No valid heartbeat pattern found in raw_data")
        return None

    def process_raw_data(
        self,
        data,
        symbol="Unknown",
        timeframe="Unknown",
        chart_id=None,
        data_type="NONE",
    ):
        try:
            candles = []
            series_key = self.series_keys.get(chart_id)

            if "p" in data:
                series_data = data["p"][1] if len(data["p"]) > 1 else data["p"][0]
                if isinstance(series_data, dict):
                    if series_key and series_key in series_data:
                        candles = series_data[series_key].get("s", [])
                    else:
                        for key in series_data:
                            if key.startswith("sds_") and "s" in series_data[key]:
                                candles = series_data[key]["s"]
                                if chart_id and chart_id not in self.series_keys:
                                    self.series_keys[chart_id] = key
                                    logger.debug(
                                        f"Cập nhật series_key cho {symbol}: {key}"
                                    )
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
                                    if chart_id and chart_id not in self.series_keys:
                                        self.series_keys[chart_id] = key
                                        logger.debug(
                                            f"Cập nhật series_key cho {symbol}: {key}"
                                        )
                                break

            if not candles:
                logger.debug(
                    f"Không có dữ liệu nến cho {symbol} (Timeframe: {timeframe}, Type: {data_type})"
                )
                return []

            processed = []
            for candle in candles:
                try:
                    values = candle.get("v", [])
                    if len(values) >= 5:
                        sanitized_symbol = (
                            symbol.replace("&", "AND") if symbol else "Unknown"
                        )
                        processed.append(
                            {
                                "provider:symbol": sanitized_symbol,
                                "timeframe": timeframe,
                                "timestamp": datetime.fromtimestamp(
                                    values[0], tz=pytz.UTC
                                ).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3],
                                "open": float(values[1]),
                                "high": float(values[2]),
                                "low": float(values[3]),
                                "close": float(values[4]),
                                "volume": float(values[5]),
                                "type": data_type,
                            }
                        )
                except (IndexError, ValueError, TypeError) as e:
                    logger.debug(f"Lỗi xử lý nến cho {symbol}: {e}")
                    continue
            if not processed:
                logger.debug(
                    f"Không có nến hợp lệ sau khi xử lý cho {symbol} (Timeframe: {timeframe}, Type: {data_type})"
                )
            return processed
        except Exception as e:
            logger.error(
                f"Lỗi xử lý dữ liệu cho {symbol} (Timeframe: {timeframe}): {e}"
            )
            return []

    async def restore_configuration(self):
        if not self.connected:
            logger.warning("Kết nối không ổn định")
            return
        if self.auth_token:
            await self.send_auth_token(self.auth_token)
        if self.configured_symbols:
            self.symbol_to_chart = {}
            self.series_keys = {}
            self.quote_to_symbol = {}
            self.active_charts = {}

    async def send_auth_token(self, token):
        return await self.send({"m": "set_auth_token", "p": [token]})

    async def send(self, message, retry=False):
        try:
            if not self.connected or not self.ws:
                logger.error("Không có kết nối WebSocket để gửi tin nhắn")
                return False
            if isinstance(message, str):
                await self.ws.send(message)
            else:
                ms = json.dumps(message)
                await self.ws.send(f"~m~{len(ms)}~m~{ms}")
            self.last_message_time = time.time()
            return True
        except Exception as e:
            logger.error(f"Lỗi gửi: {e}")
            self.connected = False
            return False

    async def send_async(self, message, retry=False):
        return await self.send(message, retry)

    async def recv(self, timeout=0.1):
        try:
            if not self.connected or not self.ws:
                return None  # Không log để tránh spam
            async with asyncio.timeout(timeout):
                data = await self.ws.recv()
                self.last_message_time = time.time()
                return data
        except websockets.exceptions.ConnectionClosedOK as e:
            logger.info(f"Kết nối đóng bình thường (code 1000 OK): {e}")
            self.connected = False
            return None
        except websockets.exceptions.ConnectionClosedError as e:
            logger.error(f"Kết nối đóng với lỗi (code {e.code}): {e}")
            self.connected = False
            return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Lỗi nhận khác: {e}")
            self.connected = False
            return None

    async def handle_save_completion(self, save_task, symbol, timeframe):
        """Xử lý khi hoàn thành lưu dữ liệu và dọn dẹp tài nguyên"""
        try:
            save_success = await save_task
            if save_success:
                async with self.processing_lock:
                    # Cập nhật trạng thái đã hoàn thành
                    self.completed_symbols.add((symbol, timeframe))
                    completed = len(self.completed_symbols)
                    total = self.total_symbols
                    remaining = total - completed

                    # Ghi log tiến độ
                    logger.info(
                        f"✅ Đã lưu xong {symbol} {timeframe}m | "
                        f"Tiến độ: {completed}/{total} (Còn lại: {remaining})"
                    )

                    # Dọn dẹp tài nguyên
                    # chart_key = f"{symbol}"
                    # chart_info = self.active_charts.pop(chart_key, None)
                    # if chart_info:
                    #     chart_id, series_key, _ = chart_info
                    #     await self.remove_setup(symbol, chart_id, timeframe, series_key)
                    #     logger.info(
                    #         f"🔧 Đã xóa setup của {symbol} (Timeframe: {timeframe}m)"
                    #     )
                    # else:
                    #     logger.warning(
                    #         f"Không tìm thấy thông tin chart cho {chart_key} để xóa"
                    #     )

                    # Kiểm tra nếu đã xử lý xong tất cả
                    if total > 0 and completed >= total:
                        logger.info(
                            f"🎉 Đã xử lý xong {completed}/{total} cặp symbol-timeframe!"
                        )
                        # Ghi config last_time vào file

                        save_historical_config_last_time()

                        await self.shutdown_gracefully()
                    elif total == 0:
                        logger.warning("Không có symbol nào để xử lý. Đang dừng...")
                        await self.shutdown_gracefully()
            else:
                logger.error(f"❌ Lỗi khi lưu dữ liệu {symbol} {timeframe}m")

        except Exception as e:
            logger.error(f"Lỗi khi xử lý kết quả lưu {symbol} {timeframe}m: {e}")

    async def shutdown_gracefully(self):
        """Dừng chương trình một cách an toàn sau khi hoàn thành tất cả tác vụ"""
        logger.info("Đang dừng chương trình một cách an toàn...")
        self.keep_running = False
        await self.close()

    async def close(self):
        """Đóng kết nối WebSocket một cách an toàn."""
        if not hasattr(self, "ws") or self.ws is None:
            logger.info("Không có kết nối WebSocket nào đang mở")
            return

        logger.info("Bắt đầu đóng kết nối...")
        self.keep_running = False

        try:
            if not self.ws.closed:
                logger.info("Đang đóng kết nối WebSocket...")
                await self.ws.close()
                logger.info("Đã đóng kết nối WebSocket")
        except Exception as e:
            logger.error(f"Lỗi khi đóng WebSocket: {e}")
        finally:
            self.ws = None
            self.connected = False
            logger.info("Đã đóng tất cả kết nối")

    async def setup_symbol(self, symbol, timeframe, retries=3):
        for attempt in range(retries):
            try:
                chart_id = f"cs_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}"
                quote_id = f"qs_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}"
                quote_id_2 = f"qs_{''.join(random.choices(string.ascii_letters + string.digits, k=12))}"
                self.symbol_to_chart[f"{symbol}:{timeframe}"] = (chart_id, timeframe)
                self.quote_to_symbol[quote_id] = symbol

                tf_call = timeframe
                series_key = f"sds_{len(self.symbol_to_chart)}"
                messages = [
                    {"m": "chart_create_session", "p": [chart_id, ""]},
                    {"m": "switch_timezone", "p": [chart_id, "Etc/UTC"]},
                    {
                        "m": "resolve_symbol",
                        "p": [
                            chart_id,
                            f"sds_sym_{len(self.symbol_to_chart)}",
                            f"={json.dumps({'adjustment': 'splits', 'currency-id': 'USD', 'session': 'regular', 'symbol': symbol})}",
                        ],
                    },
                    {
                        "m": "create_series",
                        "p": [
                            chart_id,
                            series_key,
                            "s1",
                            f"sds_sym_{len(self.symbol_to_chart)}",
                            tf_call,
                            self.bars,
                            "",
                        ],
                    },
                ]

                for msg in messages:
                    if not await self.send(msg):
                        logger.error(
                            f"Gửi tin nhắn thất bại cho {symbol} (Thử {attempt + 1}/{retries}): {msg}"
                        )
                        raise Exception("Gửi tin nhắn thất bại")
                logger.info(
                    f"Cấu hình thành công cho {symbol} (Timeframe: {timeframe}m) - chart_id: {chart_id}"
                )
                return True, chart_id, series_key
            except Exception as e:
                logger.error(
                    f"Lỗi cài đặt {symbol} (Timeframe: {timeframe}, Thử {attempt + 1}/{retries}): {e}"
                )
                if attempt < retries - 1:
                    await asyncio.sleep(0.2)
                    continue
                return False, None
        logger.error(f"Không thể cài đặt {symbol} sau {retries} lần thử")
        return False, None

    async def remove_setup(self, symbol, chart_id, timeframe, series_key):
        try:
            remove_msg = {"m": "remove_series", "p": [chart_id, series_key]}
            await asyncio.sleep(0.2)  # Delay nhỏ để đảm bảo create_series được xử lý
            if await self.send(remove_msg):
                logger.info(f"Đã tắt realtime cho {symbol} {timeframe}m ngay sau setup")
            else:
                logger.warning(f"Không thể tắt realtime cho {symbol} {timeframe}m")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi xóa {symbol} {chart_id}: {e}")
            return False

    async def delete_symbol(self, symbol, chart_id, timeframe):
        try:
            messages = {"m": "study_deleted", "p": [chart_id, ""]}
            await self.send(messages)
            logger.info(f"Xóa cấu hình thành công cho {symbol} - Timeframe {timeframe}")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi xóa {symbol} {chart_id}: {e}")
            return False


async def process_setup(
    ws: TradingViewWebSocket, configured_symbols: list[tuple[str, str]]
) -> None:
    """Xử lý cấu hình cho tất cả các timeframe và symbol với thời gian chờ hợp lý"""
    all_timeframes = sorted(
        set(tf for _, tf in configured_symbols),
        key=lambda x: int(x) if x.isdigit() else float("inf"),
    )

    # Initialize saved_data for all symbol/timeframe combinations
    ws.saved_data = {(symbol, tf): False for symbol, tf in configured_symbols}
    ws.total_pairs = len(configured_symbols)
    logger.info(f"Tổng số cặp symbol-timeframe cần xử lý: {ws.total_pairs}")

    for current_tf in all_timeframes:
        # Kiểm tra kết nối trước mỗi lần xử lý timeframe
        if not ws.keep_running or not ws.connected:
            logger.warning(f"Mất kết nối, dừng tại timeframe {current_tf}m")
            break

        logger.info(f"Đang xử lý timeframe {current_tf}m...")
        current_symbols = [s for s, tf in configured_symbols if tf == current_tf]

        for symbol in current_symbols:
            if not ws.connected:
                logger.warning(f"Mất kết nối khi đang xử lý {symbol}")
                break

            try:
                # Thêm thời gian chờ giữa các lần gửi yêu cầu
                await asyncio.sleep(0.2)

                success, chart_id, series_key = await ws.setup_symbol(
                    symbol, current_tf
                )
                if success:
                    # Store chart info with symbol:timeframe as key
                    chart_key = f"{symbol}:{current_tf}"
                    ws.active_charts[chart_key] = (chart_id, series_key, current_tf)
                    logger.debug(f"Đã lưu thông tin chart cho {chart_key}")

            except Exception as e:
                logger.error(f"Lỗi khi cài đặt {symbol}: {e}")
                continue

        if ws.connected:
            if ws.bars > 5000:
                logger.info(f"Đã xử lý xong timeframe {current_tf}m, đợi 50s...")
                await asyncio.sleep(40)
            else:
                logger.info(f"Đã xử lý xong timeframe {current_tf}m, đợi 8s...")
                await asyncio.sleep(8)


async def main():
    script_name = os.path.basename(__file__)
    set_console_title(f"TradingView Historical - {script_name}")

    # Khởi tạo log file path
    log_file = os.path.join(BASE_DIR, NAME_LOG_FILE)
    logger.info("Bắt đầu dọn dẹp log file...")
    cleanup_old_logs(log_file)
    logger.info("Hoàn thành dọn dẹp log file")

    global ws_instance
    ws = ws_instance

    # Load lasttime historical config
    historical_config = load_historical_config()
    last_time_historical_config = historical_config["last_time"]

    ws.bars = 30000

    if last_time_historical_config:
        time_difference = datetime.now() - datetime.strptime(
            last_time_historical_config, "%Y-%m-%d %H:%M:%S"
        )
        time_difference_minutes = int(time_difference.total_seconds() / 60)
        required_bars = max(1, time_difference_minutes)

        ws.bars = required_bars + 300 if required_bars <= 300 else required_bars

    csv_path = None
    if not os.environ.get("GEN_TOKEN_DIR"):
        csv_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "nodeapp",
            "websocket_tokens.csv",
        )
        print(csv_path)

    else:
        if getattr(sys, "frozen", False):
            # If running as exe, use the directory containing the exe
            base_dir = os.path.dirname(sys.executable)
            # Look for gen_token in the same directory as the exe
            gen_token_dir = os.path.join(base_dir, "..", "gen_token")
            csv_path = os.path.abspath(
                os.path.join(gen_token_dir, "websocket_tokens.csv")
            )
        else:
            # If running as script, use the project root
            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            # Look in dist/gen_token first, then fall back to gen_token
            csv_path = os.path.abspath(
                os.path.join(base_dir, "dist", "gen_token", "websocket_tokens.csv")
            )
            if not os.path.exists(csv_path):
                csv_path = os.path.abspath(
                    os.path.join(base_dir, "gen_token", "websocket_tokens.csv")
                )

        print(f"Đường dẫn file token: {csv_path}")

    try:
        df = pd.read_csv(csv_path)
        if not df.empty and "token" in df.columns:
            auth_token = df["token"].iloc[-1]
            encode_cookies = df["cookies"].iloc[-1]
            ws.auth_cookies = base64.b64decode(encode_cookies).decode("utf-8")
            ws.store_auth_token(auth_token)
            logger.info("Successfully loaded authentication token from CSV")
        else:
            logger.error(
                "Invalid or empty CSV file format. Expected columns: 'token' and 'cookies'"
            )
            return
    except Exception as e:
        logger.error(
            f"Error reading authentication token from CSV: {str(e)}\nPlease ensure the file exists at: {csv_path}"
        )
        return

    raw_timeframe = await trading_service.get_timeframe_list()
    TIMEFRAMES = {
        str(tf[1]): (tf[0], int(tf[1])) if tf[1].isdigit() else (tf[0], tf[2] // 60)
        for tf in raw_timeframe
    }
    logger.info(f"Timeframes: {TIMEFRAMES}")

    raw_symbols = await trading_service.get_assets_list()
    raw_symbols = [
        (s[0].replace("&", "AND") if s[0] else None,)
        for s in raw_symbols
        if s[0] is not None
    ]

    if not raw_symbols:
        logger.error("No valid symbols retrieved from database")
        return

    configured_symbols = []
    # for s in raw_symbols:
    #     symbol = s[0].split(":")[0] + ":" + s[0].split(":")[1]
    #     for tf_call in TIMEFRAMES:
    #         configured_symbols.append((symbol, tf_call))

    for s in raw_symbols:
        symbol = s[0].split(":")[0] + ":" + s[0].split(":")[1]
        for tf_call, (tf_type, tf_minutes) in TIMEFRAMES.items():
            configured_symbols.append((symbol, tf_call))

    print(f"Total symbols from DB: {len(configured_symbols)} ")
    print(f"Raw symbols from DB: {configured_symbols} ")

    ws.store_symbols(configured_symbols)

    if not await ws.connect():
        logger.error("Không thể kết nối tới WebSocket")
        sys.exit(1)

    await ws.send_auth_token(auth_token)

    # for symbol, timeframe in configured_symbols:
    #     success, chart_id, series_key = await ws.setup_symbol(symbol, timeframe)
    #     if not success:
    #         logger.error(f"Không thể cài đặt {symbol} (Timeframe: {timeframe}m)")

    timeframe_task = asyncio.create_task(process_setup(ws, configured_symbols))

    last_monitor_time = time.time()

    try:
        while ws.keep_running:
            if not ws.connected:
                logger.error("Kết nối WebSocket đã mất, thoát vòng lặp chính")
                break
            res = await ws.recv()

            if not res or not res.strip():
                continue

            if "~h~" in res:
                # logger.info(res)
                heartbeat_msg = ws.extract_heartbeat(res)
                if heartbeat_msg:
                    logger.info(f"Heartbeat received: {heartbeat_msg}")
                    try:
                        asyncio.create_task(ws.send(heartbeat_msg))
                        logger.info(f"Echoed heartbeat: {heartbeat_msg}")
                    except Exception as e:
                        logger.error(f"Failed to echo heartbeat: {e}")

            messages = ws._parse_messages(res)

            for message in messages:
                try:
                    # Kiểm tra message trước khi parse
                    if not message or message.isspace():
                        logger.debug("Bỏ qua message rỗng")
                        continue
                    if not (message.startswith("{") or message.startswith("[")):
                        logger.debug(f"Bỏ qua message không phải JSON: {message}")
                        continue
                    data = json.loads(message)
                    message_type = data.get("m", "")
                    chart_id = data.get("p", [None])[0]
                    symbol_info = next(
                        (
                            (s, t)
                            for s, (c, t) in ws.symbol_to_chart.items()
                            if c == chart_id
                        ),
                        ("Unknown", "Unknown"),
                    )
                    symbol, timeframe = symbol_info

                    if message_type == "timescale_update":
                        processed_data = ws.process_raw_data(
                            data, symbol, timeframe, chart_id, data_type="HISTORICAL"
                        )
                        if processed_data:

                            df = pd.DataFrame(processed_data)
                            df.insert(0, "No", range(1, len(df) + 1))

                            try:
                                base_symbol = (
                                    symbol.split(":")[0] + ":" + symbol.split(":")[1]
                                )
                                matching_raw = next(
                                    (
                                        rs[0]
                                        for rs in raw_symbols
                                        if rs[0] and rs[0].startswith(base_symbol)
                                    ),
                                    None,
                                )

                                if not matching_raw:
                                    logger.warning(
                                        f"Không tìm thấy thông tin chi tiết cho symbol: {base_symbol}"
                                    )
                                    continue

                                parts = matching_raw.split(":")
                                if len(parts) >= 4:
                                    provider_code = parts[0]
                                    symbol_name = parts[1]
                                    asset_id = parts[2]
                                    provider_id = parts[3]
                                    symbol_name = symbol_name.replace("&", "AND")
                                    df["symbol"] = f"{provider_code}:{symbol_name}"
                                    df["asset_id"] = asset_id
                                    df["provider_id"] = provider_id
                                    df["timeframe"] = timeframe
                                else:
                                    logger.warning(
                                        f"Định dạng raw_symbol không hợp lệ: {matching_raw}"
                                    )
                                    continue
                            except Exception as e:
                                logger.error(f"Lỗi khi xử lý thông tin symbol: {e}")
                                continue

                            logger.debug(
                                f"Input df provider:symbol: {df['provider:symbol'].tolist()}"
                            )
                            df["symbol"] = (
                                df["provider:symbol"]
                                .str.split(":")
                                .str[1]
                                .fillna("Unknown")
                                .str.replace("&", "AND", regex=False)
                            )
                            df["asset_id"] = df["asset_id"].fillna("0")
                            df["provider_id"] = df["provider_id"].fillna("0")
                            df["timeframe_type"] = TIMEFRAMES.get(timeframe, ("M1", 1))[
                                0
                            ]
                            df["provider_code"] = provider_code
                            df["date_time"] = df["timestamp"]
                            columns_order = [
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
                            df = df[columns_order]
                            #######
                            if df.__len__() > 50:

                                ##### lấy nến cuối của df, sau đó lấy date_time của nến cuối đó và chuyển thành thời gian ngày tháng năm giờ và phút
                                ##### nếu như date_time của nến cuối đó nhỏ hơn thời gian hiện tại thì lấy chọn df còn không thì chọn df trừ đi 1 nến

                                # df_datetime = (
                                #     pd.to_datetime(df["date_time"].iloc[-1])
                                #     .replace(second=0, microsecond=0)
                                #     .tz_localize("UTC")
                                # )
                                # current_utc = datetime.now(timezone.utc).replace(
                                #     second=0, microsecond=0
                                # )

                                # choose_df = (
                                #     df if df_datetime < current_utc else df.iloc[:-1]
                                # )
                                df = df.iloc[:-1]

                                logger.info(
                                    f"[HISTORICAL] DỮ LIỆU OHLCV - {symbol} (Timeframe: {timeframe}m)"
                                )
                                logger.debug(
                                    f"Processed {symbol} data for {timeframe}m"
                                )
                                logger.info(df.tail(3).to_string(index=False))
                                print("=" * 80)

                                # Create and track the save task
                                save_task = asyncio.create_task(
                                    save_with_retry_simple(df.copy(), symbol, timeframe)
                                )
                                # Handle the completion of the save task
                                asyncio.create_task(
                                    ws.handle_save_completion(
                                        save_task, symbol, timeframe
                                    )
                                )

                    # elif message_type == "du":
                    #     processed_data = ws.process_raw_data(
                    #         data, symbol, timeframe, chart_id, data_type="REALTIME"
                    #     )
                    #     if processed_data:
                    #         logger.info(
                    #             f"[REALTIME] DỮ LIỆU OHLCV - {symbol} (Timeframe: {timeframe}m)"
                    #         )
                    #         df = pd.DataFrame(processed_data)
                    #         df.insert(0, "No", range(1, len(df) + 1))
                    #         logger.info(df.tail(3).to_string(index=False))
                    #         logger.info("=" * 80)

                    current_time = time.time()
                    if current_time - last_monitor_time > 10:
                        logger.info("WebSocket vẫn hoạt động")
                        last_monitor_time = current_time

                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(f"Lỗi parse message: {e}, Message: {message}")
                    continue
    except KeyboardInterrupt:
        logger.info("Chương trình đang tắt...")
    finally:
        timeframe_task.cancel()
        try:
            await timeframe_task
        except asyncio.CancelledError:
            pass
        await ws.close()


async def save_with_retry_simple(df: pd.DataFrame, symbol: str, timeframe: str) -> bool:
    """Phiên bản đơn giản hóa của retry logic"""
    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            success = await trading_service.handle_olvc_historical_to_db_session(
                df, symbol, timeframe
            )

            if success:
                return True
            else:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {symbol} {timeframe}"
                    )
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
                else:
                    logger.error(
                        f"Failed after {max_retries} attempts: {symbol} {timeframe}"
                    )
                    return False

        except Exception as e:
            logger.error(
                f"Error on attempt {attempt + 1} for {symbol} {timeframe}: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (attempt + 1))
            else:
                return False

    return False


def safe_save_async(df: pd.DataFrame, symbol: str, timeframe: str) -> None:
    """Run save operation in background without waiting for completion"""

    async def _save():
        try:
            await save_with_retry(df.copy(), symbol, timeframe)
        except Exception as e:
            logger.error(f"Lỗi khi lưu dữ liệu {symbol} {timeframe}m: {e}")

    # Tạo task mới và không cần await
    task = asyncio.create_task(_save())

    # Thêm callback để xử lý lỗi nếu có
    def log_done(future):
        if future.exception():
            logger.error(f"Lỗi trong background task: {future.exception()}")
        else:
            logger.debug(f"Hoàn thành lưu {symbol} {timeframe}m")

    task.add_done_callback(log_done)


async def save_with_retry(
    df: pd.DataFrame, symbol: str, timeframe: str, retry_count: int = 0
) -> bool:
    """Lưu dữ liệu với retry NON-BLOCKING - không block main loop"""
    try:
        success = await trading_service.handle_olvc_historical_to_db_session2(
            df, symbol, timeframe
        )
        if success:
            logger.info(f"✅ Lưu thành công {symbol} {timeframe}m")
            return True
        else:
            # Service fail - spawn background retry
            if retry_count < MAX_RETRIES:
                logger.warning(
                    f"⚠️ Lưu fail lần {retry_count + 1}/{MAX_RETRIES}, spawn retry background..."
                )
                asyncio.create_task(
                    _background_retry(df.copy(), symbol, timeframe, retry_count + 1)
                )
                return False  # Return ngay, không chờ
            else:
                logger.error(
                    f"💥 Lưu fail sau {MAX_RETRIES} lần: {symbol} {timeframe}m"
                )
                return False

    except Exception as e:
        error_msg = str(e).lower()
        logger.error(
            f"❌ Lỗi SQL {symbol} {timeframe}m lần {retry_count + 1}/{MAX_RETRIES}: {e}"
        )

        # Spawn background retry cho lỗi SQL
        if retry_count < MAX_RETRIES and any(
            keyword in error_msg
            for keyword in ["transaction", "rollback", "connection", "timeout"]
        ):
            delay = RETRY_DELAY * (retry_count + 1)
            logger.info(
                f"🔄 Spawn retry sau {delay}s (lần {retry_count + 2}/{MAX_RETRIES})..."
            )
            asyncio.create_task(
                _background_retry(df.copy(), symbol, timeframe, retry_count + 1)
            )
            return False  # Return ngay, không chờ
        else:
            logger.error(f"💥 Không retry: {symbol} {timeframe}m")
            return False


async def _background_retry(
    df: pd.DataFrame, symbol: str, timeframe: str, retry_count: int
):
    """Retry logic chạy background - không block main loop"""
    try:
        # Delay trước khi retry
        delay = RETRY_DELAY * retry_count
        await asyncio.sleep(delay)

        # Thử lại
        success = await trading_service.handle_olvc_historical_to_db_session2(
            df, symbol, timeframe
        )
        if success:
            logger.info(
                f"✅ Background retry thành công {symbol} {timeframe}m (lần {retry_count + 1})"
            )
            # Mark as completed trong background
            asyncio.create_task(
                ws_instance.handle_save_completion(
                    asyncio.Future().set_result(True), symbol, timeframe
                )
            )
        else:
            # Tiếp tục retry nếu chưa hết
            if retry_count < MAX_RETRIES:
                logger.warning(
                    f"⚠️ Background retry fail lần {retry_count + 1}/{MAX_RETRIES}, tiếp tục..."
                )
                await _background_retry(df, symbol, timeframe, retry_count + 1)
            else:
                logger.error(
                    f"💥 Background retry fail hết {MAX_RETRIES} lần: {symbol} {timeframe}m"
                )
                # Mark as failed
                asyncio.create_task(
                    ws_instance.handle_save_completion(
                        asyncio.Future().set_result(False), symbol, timeframe
                    )
                )

    except Exception as e:
        logger.error(f"❌ Background retry lỗi {symbol} {timeframe}m: {e}")
        if retry_count < MAX_RETRIES:
            await _background_retry(df, symbol, timeframe, retry_count + 1)


def get_writable_config_path():
    """Get a writable path for the config file.
    When running as EXE, always use the same directory as the EXE.
    When running as script, use the script's directory.
    """
    try:
        if getattr(sys, "frozen", False):
            # When running as EXE, always use the EXE's directory
            exe_dir = Path(sys.executable).parent
            config_path = exe_dir / "historical_config.json"

            # Create the file if it doesn't exist
            if not config_path.exists():
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump({"last_time": ""}, f, indent=4)

            return config_path
        else:
            # For development, use the same directory as the script
            return historical_config_path

    except Exception as e:
        logger.error(f"Lỗi khi xác định đường dẫn cấu hình: {e}")
        # Fallback to current directory if everything else fails
        return Path.cwd() / "historical_config.json"


def save_historical_config_last_time():
    try:
        # Get writable config path
        config_path = get_writable_config_path()
        logger.info(f"Đường dẫn cấu hình sẽ được ghi: {config_path}")
        logger.info(f"Đường dẫn cấu hình gốc: {historical_config_path}")

        # First load the current config
        current_config = {}

        # Try to load from the original config path first
        if os.path.exists(historical_config_path):
            try:
                with open(historical_config_path, "r", encoding="utf-8") as f:
                    current_config = json.load(f)
                logger.info("Đã tải cấu hình từ đường dẫn gốc")
            except Exception as e:
                logger.error(f"Lỗi khi đọc file cấu hình gốc: {e}")

        # Then try to load from the writable config path
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    current_config.update(json.load(f))
                logger.info("Đã cập nhật cấu hình từ đường dẫn ghi được")
            except Exception as e:
                logger.error(f"Lỗi khi đọc file cấu hình ghi được: {e}")

        # Update only the last_time field
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_config["last_time"] = current_time
        logger.info(f"Đang cập nhật thời gian: {current_time}")

        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)

        # Save to the writable location
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(current_config, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            # Verify the file was written
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    saved_config = json.load(f)
                    if saved_config.get("last_time") == current_time:
                        logger.info(
                            f"✅ Đã cập nhật last_time thành công: {current_time}"
                        )
                        logger.info(
                            f"✅ File được lưu tại: {os.path.abspath(config_path)}"
                        )
                        return True
                    else:
                        logger.error("❌ Lỗi xác minh: Giá trị last_time không khớp")
            else:
                logger.error("❌ Lỗi: Không tìm thấy file sau khi ghi")

        except Exception as e:
            logger.error(f"❌ Lỗi khi ghi file cấu hình: {e}")
            logger.error(f"Loại lỗi: {type(e).__name__}")
            logger.error(f"Thông tin chi tiết: {str(e)}")

    except Exception as e:
        logger.error(
            f"❌ Lỗi không xác định trong save_historical_config_last_time: {e}"
        )
        logger.error(f"Loại lỗi: {type(e).__name__}")
        logger.error(f"Thông tin chi tiết: {str(e)}")

    return False


ws_instance = None


def main_async():
    global ws_instance
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        ws_instance = TradingViewWebSocket()
        main_task = loop.create_task(main())

        try:
            loop.run_until_complete(main_task)

            # Check if all data has been saved
            if hasattr(ws_instance, "saved_data") and all(
                ws_instance.saved_data.values()
            ):
                logger.info("Đã lưu xong dữ liệu cho tất cả symbol và timeframe.")

        except (KeyboardInterrupt, SystemExit) as e:
            logger.info(
                f"Đang dừng chương trình: {str(e) or 'Nhận được tín hiệu dừng'}"
            )
        except Exception as e:
            logger.error(f"Lỗi không mong muốn: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Lỗi khởi tạo: {e}", exc_info=True)
    finally:
        try:
            # Close WebSocket if it's still open
            if (
                ws_instance
                and hasattr(ws_instance, "ws")
                and ws_instance.ws is not None
            ):
                logger.info("Đang đóng kết nối WebSocket...")
                loop.run_until_complete(ws_instance.close())

            # Cancel all pending tasks
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            if pending:
                logger.info(f"Đang hủy {len(pending)} task đang chạy...")
                for task in pending:
                    task.cancel()

                # Run the loop to process cancellations
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

        except Exception as e:
            logger.error(f"Lỗi khi dọn dẹp: {e}", exc_info=True)
        finally:
            try:
                # Stop the event loop
                if loop.is_running():
                    loop.stop()

                # Shutdown async generators
                loop.run_until_complete(loop.shutdown_asyncgens())

            except Exception as e:
                logger.error(f"Lỗi khi dừng event loop: {e}")
            finally:
                if not loop.is_closed():
                    loop.close()
                logger.info("Chương trình đã dừng hoàn toàn")
                # Force exit to ensure the program terminates
                os._exit(0)


if __name__ == "__main__":
    try:
        main_async()
    except KeyboardInterrupt:
        logger.info("Đang tắt chương trình...")
    except Exception as e:
        logger.error(f"Lỗi không mong muốn: {e}")
