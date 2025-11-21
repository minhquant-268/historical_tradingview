const WebSocket = require('ws');
const moment = require('moment-timezone');
const logger = require('../utils/logger');
const Helper = require('../utils/helper');
const helper = new Helper();

class Config {
    static WEBSOCKET_URL = 'wss://prodata.tradingview.com/socket.io/websocket'; // Kiểm tra URL này
    static PING_INTERVAL = 10;
    static PING_TIMEOUT = 30;
    static MAX_BARS = 50;
    static MAX_RETRIES = 2;
    static MAX_RECONNECT_ATTEMPTS = 2;
    static MAX_TRIGGER_RETRIES = 2;
    static INITIAL_RECONNECT_DELAY = 1000; // Tăng lên 1000ms
    static CONNECTION_TIMEOUT = 5000; // Tăng lên 5000ms
}

class TradingViewWebSocket {
    constructor() {
        this.ws = null;
        this.connected = false;
        this.keepRunning = true;
        this.pingInterval = Config.PING_INTERVAL;
        this.pingTimeout = Config.PING_TIMEOUT;
        this.lastMessageTime = Date.now() / 1000;
        this.bars = Config.MAX_BARS;
        this.authToken = null;
        this.authCookies = null;
        this.configuredSymbols = [];
        this.symbolToChart = {};
        this.seriesKeys = {};
        this.quoteToSymbol = {};
        this.activeCharts = {};
        this.savedData = {};
        this.completedSymbols = new Set();
        this.totalSymbols = 0;
        this.reconnectAttempts = 0;
        this.messageHandler = null;
    }

    async connect() {
        if (getShutdownFlag()) {
            logger.warn('Cờ tắt được bật, không thể kết nối');
            return false;
        }

        try {
            if (this.ws && this.ws.readyState !== WebSocket.CLOSED) {
                logger.debug('Đóng kết nối hiện tại trước khi kết nối mới');
                try {
                    this.ws.terminate();
                } catch (e) {
                    logger.debug(`Lỗi khi đóng WS cũ: ${e.message}`);
                }
                this.ws = null;
                this.connected = false;
            }

            logger.debug(`Thử kết nối tới WebSocket: ${Config.WEBSOCKET_URL}`);
            this.ws = new WebSocket(Config.WEBSOCKET_URL, {
                headers: {
                    Origin: 'https://www.tradingview.com/'
                },
                timeout: Config.CONNECTION_TIMEOUT
            });

            return await new Promise((resolve, reject) => {
                const timeoutId = setTimeout(() => {
                    if (this.ws) {
                        this.ws.terminate();
                        this.ws = null;
                    }
                    reject(new Error('Kết nối WebSocket hết thời gian'));
                }, Config.CONNECTION_TIMEOUT);

                this.ws.on('open', () => {
                    clearTimeout(timeoutId);
                    this.connected = true;
                    this.lastMessageTime = Date.now() / 1000;
                    this.reconnectAttempts = 0;
                    this.setupEventHandlers();
                    logger.info('✅ Kết nối WebSocket thành công');
                    resolve(true);
                });

                this.ws.on('error', (e) => {
                    clearTimeout(timeoutId);
                    logger.error(`❌ Lỗi WebSocket: ${e.message}`);
                    this.connected = false;
                    if (this.ws) this.ws = null;
                    reject(e);
                });
            });
        } catch (e) {
            logger.error(`❌ Kết nối thất bại: ${e.message}`);
            this.connected = false;
            if (this.ws) this.ws = null;
            return false;
        }
    }

    setupEventHandlers() {
        if (!this.ws) return;
        this.ws.removeAllListeners();

        this.ws.on('close', (code, reason) => {
            if (!this.keepRunning || getShutdownFlag()) {
                logger.debug(`🔌 Đóng kết nối như dự kiến khi tắt: code ${code || 1005}`);
                this.connected = false;
                return;
            }
            logger.warn(`🔌 Kết nối bị đóng: code ${code || 1005}, lý do: ${reason || 'KHÔNG_CÓ_LÝ_DO'}`);
            this.connected = false;
        });

        this.ws.on('error', (e) => {
            logger.error(`❌ Lỗi WS: ${e.message}`);
            this.connected = false;
        });
    }

    setMessageHandler(handler) {
        this.messageHandler = handler;
        if (this.ws) {
            this.ws.removeAllListeners('message');
            this.ws.on('message', handler);
        }
    }

    storeAuthToken(token) {
        logger.info(`Lưu token xác thực: ${token}`);
        this.authToken = token;
    }

    storeSymbols(symbols) {
        this.configuredSymbols = symbols;
        this.totalSymbols = symbols.length;
        logger.info(`📊 Lưu ${this.totalSymbols} cặp biểu tượng-khung thời gian`);
    }

    _parseMessages(rawData) {
        const messages = [];
        let pos = 0;
        while (pos < rawData.length && rawData.startsWith('~m~', pos)) {
            try {
                const secondDelim = rawData.indexOf('~m~', pos + 3);
                if (secondDelim === -1) break;
                const length = parseInt(rawData.slice(pos + 3, secondDelim));
                const start = secondDelim + 3;
                const end = start + length;
                if (end > rawData.length) break;
                const message = rawData.slice(start, end);
                if (message.startsWith('~h~') || !message.trim()) {
                    pos = end;
                    continue;
                }
                messages.push(message);
                pos = end;
            } catch (e) {
                logger.error(`Lỗi phân tích tin nhắn WebSocket: ${e.message}`);
                break;
            }
        }
        return messages;
    }

    extractHeartbeat(rawData) {
        if (!rawData || !rawData.includes('~h~')) return null;
        const pattern = /~m~(\d+)~m~~h~(\d+)/g;
        let match;
        while ((match = pattern.exec(rawData)) !== null) {
            const x = parseInt(match[1]);
            const y = match[2];
            const payload = `~h~${y}`;
            if (x === payload.length) {
                logger.debug(`Phát hiện heartbeat hợp lệ: ${payload}`);
                return `~m~${x}~m~${payload}`;
            }
        }
        return null;
    }

    _extractCandles(data, chartId, seriesKey) {
        const candles = [];
        if (!data.p || !Array.isArray(data.p)) return candles;
        let seriesData = data.p.length > 1 ? data.p[1] : data.p[0];
        if (typeof seriesData === 'object') {
            if (seriesKey && seriesData[seriesKey]) {
                candles.push(...(seriesData[seriesKey].s || []));
            } else {
                for (let key in seriesData) {
                    if (key.startsWith('sds_') && seriesData[key].s) {
                        candles.push(...seriesData[key].s);
                        if (chartId && !this.seriesKeys[chartId]) {
                            this.seriesKeys[chartId] = key;
                        }
                        break;
                    }
                }
            }
        }
        return candles;
    }

    _parseVolume(values, symbol) {
        if (values.length <= 5) return 0.0;
        try {
            let volume = parseFloat(values[5]);
            if (volume < 0 || volume > 1000000000) return 0.0;
            return volume;
        } catch (e) {
            return 0.0;
        }
    }

    processRawData(data, symbol = 'Unknown', timeframe = 'Unknown', chartId = null, dataType = 'NONE') {
        try {
            const candles = [];
            const seriesKey = this.seriesKeys[chartId];

            if (data.p) {
                let seriesData = data.p.length > 1 ? data.p[1] : data.p[0];
                if (typeof seriesData === 'object' && seriesData !== null) {
                    if (seriesKey && seriesData[seriesKey]) {
                        candles.push(...(seriesData[seriesKey].s || []));
                    } else {
                        for (let key in seriesData) {
                            if (key.startsWith('sds_') && seriesData[key].s) {
                                candles.push(...seriesData[key].s);
                                if (chartId && !this.seriesKeys[chartId]) {
                                    this.seriesKeys[chartId] = key;
                                    logger.debug(`Cập nhật series_key cho ${symbol}: ${key}`);
                                }
                                break;
                            }
                        }
                    }
                } else if (data.m === 'timescale_update') {

                    for (let item of data.p) {
                        if (typeof item === 'object' && item !== null) {
                            if (seriesKey && item[seriesKey]) {
                                candles = item[seriesKey].s || [];
                                break;
                            }
                            for (let key in item) {
                                if (key.startsWith('sds_') && item[key].s) {
                                    candles = item[key].s;
                                    if (chartId && !this.seriesKeys[chartId]) {
                                        this.seriesKeys[chartId] = key;
                                        logger.debug(`Cập nhật series_key cho ${symbol}: ${key}`);
                                    }
                                    break;
                                }
                            }
                            break;
                        }
                    }
                }
            }

            if (!candles.length) {
                logger.debug(`Không có dữ liệu nến cho ${symbol} (Timeframe: ${timeframe}, Type: ${dataType})`);
                return [];
            }

            const processed = [];
            for (let candle of candles) {
                try {
                    const values = candle.v || [];
                    if (values.length >= 5) {
                        const sanitizedSymbol = symbol ? symbol.replace(/&/g, 'AND') : 'Unknown';
                        const timestampMs = Number(values[0]) * 1000;
                        const timestamp = moment.utc(timestampMs).format('YYYY-MM-DD HH:mm:ss,SSS');

                        processed.push({
                            'provider:symbol': sanitizedSymbol,
                            timestampMs,
                            timeframe,
                            timestamp,
                            open: parseFloat(values[1]),
                            high: parseFloat(values[2]),
                            low: parseFloat(values[3]),
                            close: parseFloat(values[4]),
                            volume: parseFloat(values[5] || 0),
                            type: dataType,
                        });
                    }
                } catch (e) {
                    logger.debug(`Lỗi xử lý nến cho ${symbol}: ${e.message}`);
                    continue;
                }
            }

            if (!processed.length) {
                logger.debug(`Không có nến hợp lệ sau khi xử lý cho ${symbol} (Timeframe: ${timeframe}, Type: ${dataType})`);
            }

            return processed;
        } catch (e) {
            logger.error(`Lỗi xử lý dữ liệu cho ${symbol} (Timeframe: ${timeframe}): ${e.message}`);
            return [];
        }
    }

    async restoreConfiguration() {
        if (!this.connected || !this.keepRunning || getShutdownFlag()) {
            return false;
        }

        try {
            if (this.authToken && await this.sendAuthToken(this.authToken)) {
                logger.info('✅ Khôi phục token xác thực thành công');
            }

            if (this.configuredSymbols.length > 0) {
                logger.info(`🔄 Khôi phục ${this.configuredSymbols.length} biểu tượng...`);
                let setupSuccess = true;
                for (let i = 0; i < this.configuredSymbols.length; i++) {
                    const [symbol, timeframe, currency] = this.configuredSymbols[i];
                    if (!this.keepRunning || getShutdownFlag()) break;
                    const [success] = await this.setupSymbol(symbol, timeframe, currency);
                    if (!success) setupSuccess = false;
                    if (i < this.configuredSymbols.length - 1) {
                        await new Promise(r => setTimeout(r, 100));
                    }
                }
                return setupSuccess;
            }
            return true;
        } catch (e) {
            logger.error(`❌ Khôi phục cấu hình thất bại: ${e.message}`);
            return false;
        }
    }

    async sendAuthToken(token) {
        return await this.send({ m: 'set_auth_token', p: [token] });
    }

    async send(message) {
        if (!this.connected || !this.ws || !this.keepRunning || getShutdownFlag()) {
            return false;
        }
        try {
            let ms = typeof message === 'string' ? message : JSON.stringify(message);
            if (typeof message !== 'string') ms = `~m~${ms.length}~m~${ms}`;
            this.ws.send(ms);
            this.lastMessageTime = Date.now() / 1000;
            return true;
        } catch (e) {
            logger.error(`❌ Lỗi gửi tin nhắn: ${e.message}`);
            this.connected = false;
            return false;
        }
    }

    async close(graceful = false) {
        if (!this.ws) {
            logger.info('Không có kết nối WebSocket đang mở');
            return;
        }

        logger.info(`🛑 Đóng kết nối WebSocket... (graceful: ${graceful})`);
        this.keepRunning = false;

        try {
            if (this.ws.readyState !== WebSocket.CLOSED) {
                if (graceful) {
                    this.ws.close(1000, 'Đóng bình thường');
                    logger.info('✅ Kết nối WebSocket đóng nhẹ nhàng');
                } else {
                    this.ws.terminate();
                    logger.info('✅ Kết nối WebSocket đóng ngay lập tức');
                }
            }
        } catch (e) {
            logger.error(`❌ Lỗi khi đóng WebSocket: ${e.message}`);
        } finally {
            if (this.ws) {
                this.ws.removeAllListeners();
                this.ws = null;
            }
            this.connected = false;
            this.symbolToChart = {};
            this.seriesKeys = {};
            this.quoteToSymbol = {};
            this.activeCharts = {};
            logger.info('✅ Trạng thái WebSocket đã được reset hoàn toàn');
        }
    }

    async setupSymbol(symbol, timeframe, currency, retries = Config.MAX_RETRIES) {
        for (let attempt = 0; attempt < retries; attempt++) {
            if (!this.keepRunning || getShutdownFlag()) return [false, null, null];
            try {
                const chartId = `cs_${helper.randomString(12)}`;
                const quoteId = `qs_${helper.randomString(12)}`;
                const seriesKey = `sds_${Object.keys(this.symbolToChart).length + 1}`;
                this.symbolToChart[`${symbol}:${timeframe}`] = [chartId, timeframe];
                this.quoteToSymbol[quoteId] = symbol;

                const messages = [
                    { m: 'chart_create_session', p: [chartId, ''] },
                    { m: 'switch_timezone', p: [chartId, 'Etc/UTC'] },
                    { m: 'resolve_symbol', p: [chartId, `sds_sym_${Object.keys(this.symbolToChart).length}`, `=${JSON.stringify({ adjustment: 'splits', 'currency-id': currency, session: 'regular', symbol })}`] },
                    { m: 'create_series', p: [chartId, seriesKey, 's1', `sds_sym_${Object.keys(this.symbolToChart).length}`, timeframe, this.bars, ''] },
                ];

                for (let msg of messages) {
                    if (!await this.send(msg)) throw new Error(`Gửi tin nhắn thất bại: ${JSON.stringify(msg)}`);
                }
                logger.info(`✅ Cấu hình thành công ${symbol} (Khung thời gian: ${timeframe}m)`);
                return [true, chartId, seriesKey];
            } catch (e) {
                logger.error(`❌ Lỗi khi thiết lập ${symbol}: ${e.message}`);
                if (attempt < retries - 1) await new Promise(r => setTimeout(r, 200));
            }
        }
        return [false, null, null];
    }

    async monitorConnection(retryCallback) {
        let reconnectRetries = 0;
        let triggerRetries = 0;

        while (this.keepRunning && !getShutdownFlag()) {
            try {
                if (!this.connected) {
                    logger.warn('⚠️ Mất kết nối, thử kết nối lại...');
                    let reconnectSuccess = false;

                    for (let attempt = 0; attempt < Config.MAX_RECONNECT_ATTEMPTS; attempt++) {
                        logger.info(`🔄 Thử kết nối lại lần ${attempt + 1}/${Config.MAX_RECONNECT_ATTEMPTS}`);
                        if (await this.connect()) {
                            if (await this.restoreConfiguration()) {
                                reconnectSuccess = true;
                                reconnectRetries = 0;
                                break;
                            }
                        }
                        logger.error(`❌ Thử kết nối lại lần ${attempt + 1} thất bại`);
                        await new Promise(r => setTimeout(r, Config.INITIAL_RECONNECT_DELAY));
                    }

                    if (!reconnectSuccess) {
                        reconnectRetries++;
                        logger.error(`❌ Tất cả thử kết nối lại thất bại (lần ${reconnectRetries}/${Config.MAX_TRIGGER_RETRIES})`);

                        if (reconnectRetries <= Config.MAX_TRIGGER_RETRIES && retryCallback) {
                            logger.info(`🔄 Kích hoạt thử lại job lần ${reconnectRetries}/${Config.MAX_TRIGGER_RETRIES}`);
                            const success = await retryCallback();
                            if (success) {
                                triggerRetries = 0;
                                reconnectRetries = 0;
                                continue;
                            }
                            triggerRetries++;
                        }

                        if (triggerRetries >= Config.MAX_TRIGGER_RETRIES) {
                            logger.error(`❌ Đạt tối đa số lần thử lại, chờ lịch chạy tiếp theo`);
                            return false;
                        }

                        await new Promise(r => setTimeout(r, 10000));
                        continue;
                    }
                }

                const currentTime = Date.now() / 1000;
                if (currentTime - this.lastMessageTime > Config.PING_INTERVAL) {
                    await this.send('~ping~');
                }

                if (currentTime - this.lastMessageTime > Config.PING_TIMEOUT) {
                    logger.warn('⏰ Không nhận phản hồi quá lâu, đánh dấu mất kết nối');
                    this.connected = false;
                }

                await new Promise(r => setTimeout(r, 2000));
            } catch (e) {
                logger.error(`❌ Lỗi giám sát: ${e.message}`);
                await new Promise(r => setTimeout(r, 2000));
            }
        }
        return true;
    }
}

let _shutdownFlag = false;
let _keepRunning = true;

function getShutdownFlag() {
    return _shutdownFlag;
}

function setShutdownFlag(value) {
    _shutdownFlag = value;
}

function getKeepRunning() {
    return _keepRunning;
}

function setKeepRunning(value) {
    _keepRunning = value;
}

module.exports = {
    TradingViewWebSocket,
    Config,
    getShutdownFlag,
    setShutdownFlag,
    getKeepRunning,
    setKeepRunning
};