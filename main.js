const fs = require('fs');
const path = require('path');
const csvParser = require('csv-parser');
const moment = require('moment-timezone');

// Import các module
const { TradingViewWebSocket, Config } = require('./services/tvc_service');
const trading_service = require('./services/trading_service');
const logger = require('./utils/logger');

const Helper = require('./utils/helper');
const helper = new Helper();

// Global variables
let ws = null;
let configuredSymbols = [];
let TIMEFRAMES = {};
let rawSymbols = [];

/**
 * Filter configuredSymbols by historical last_time (UTC).
 * Uses newCount formula for ALL timeframes (accurate regardless of scheduler run time).
 * Returns an object { filteredConfiguredSymbols, selectedTfCalls }
 */
function filterTimeframesByLastTime(configuredSymbolsInput, timeframesMap, lastTimeHistoricalConfig) {
    try {
        const lastTimeStr = lastTimeHistoricalConfig || '';
        let lastTimeUtc = null;
        if (!lastTimeStr) {
            lastTimeUtc = moment.utc().subtract(60, 'minutes');
        } else {
            lastTimeUtc = moment.utc(lastTimeStr, 'YYYY-MM-DD HH:mm:ss');
            if (!lastTimeUtc.isValid()) lastTimeUtc = moment.utc().subtract(60, 'minutes');
        }

        const lastMin = Math.floor(lastTimeUtc.valueOf() / 60000);
        const nowMin = Math.floor(moment.utc().valueOf() / 60000);

        const selectedTfCalls = new Set();
        function tfCallToMinutes(tfCall) {
            if (/^\d+$/.test(tfCall)) return parseInt(tfCall, 10);
            const map = {
                'M1':1,'M2':2,'M3':3,'M4':4,'M5':5,'M10':10,'M15':15,'M20':20,
                'M30':30,'M45':45,'M90':90,
                'H1':60,'H2':120,'H3':180,'H4':240,'H6':360,
                'D':1440,'W':10080,'MN':43200
            };
            return map[tfCall] || null;
        }

        logger.info(`Filter timeframes: last_time=${lastTimeUtc.format('YYYY-MM-DD HH:mm:ss')}, now=${moment.utc().format('YYYY-MM-DD HH:mm:ss')}`);
        
        for (const tfCall of Object.keys(timeframesMap)) {
            const tfMin = tfCallToMinutes(tfCall);
            if (!tfMin) {
                logger.warn(`Skipped timeframe ${tfCall}: cannot convert to minutes`);
                continue;
            }
            const newCount = Math.floor(nowMin / tfMin) - Math.floor(lastMin / tfMin);
            logger.debug(`TF ${tfCall}: tfMin=${tfMin}, newCount=${newCount}`);
            if (newCount > 0) selectedTfCalls.add(tfCall);
        }

        logger.info(`Selected timeframes: ${Array.from(selectedTfCalls).join(', ')}`);
        const filteredConfiguredSymbols = configuredSymbolsInput.filter(([symbol, tfCall]) => selectedTfCalls.has(tfCall));
        return { filteredConfiguredSymbols, selectedTfCalls };
    } catch (err) {
        throw err;
    }
}

/**
 * Load authentication token from CSV file
 */
async function loadAuthToken() {
    return new Promise((resolve, reject) => {
        const csvPath = path.join(__dirname, 'nodeapp', 'websocket_tokens.csv');

        if (!fs.existsSync(csvPath)) {
            reject(new Error(`Token file not found: ${csvPath}`));
            return;
        }

        let latestToken = null;
        let latestCookies = null;

        fs.createReadStream(csvPath)
            .pipe(csv())
            .on('data', (row) => {
                if (row.token && row.cookies) {
                    latestToken = row.token;
                    latestCookies = row.cookies;
                }
            })
            .on('end', () => {
                if (latestToken && latestCookies) {
                    authToken = latestToken;
                    authCookies = latestCookies;
                    logger.info('✅ Successfully loaded authentication token from CSV');
                    resolve();
                } else {
                    reject(new Error('Invalid CSV format or no valid tokens found'));
                }
            })
            .on('error', (error) => {
                reject(new Error(`Error reading CSV file: ${error.message}`));
            });
    });
}

/**
 * Process setup for all symbols and timeframes
 */
async function processSetup(ws, configuredSymbols) {    
    const allTimeframes = [...new Set(configuredSymbols.map(([, tf]) => tf))].sort((a, b) => {
        const aNum = parseInt(a) || Infinity;
        const bNum = parseInt(b) || Infinity;
        return aNum - bNum;
    });

    // Initialize saved data tracking
    ws.savedData = {};
    configuredSymbols.forEach(([symbol, tf]) => {
        ws.savedData[`${symbol}:${tf}`] = false;
    });
    ws.totalPairs = configuredSymbols.length;
    logger.info(`Total symbol-timeframe pairs to process: ${ws.totalPairs}`);

    for (const currentTf of allTimeframes) {
        if (!ws.keepRunning || !ws.connected) {
            logger.warn(`Connection lost at timeframe ${currentTf}m`);
            break;
        }

        logger.info(`Processing timeframe ${currentTf}m...`);
        const currentSymbols = configuredSymbols.filter(([, tf]) => tf === currentTf);
        console.log('Current symbols:', currentSymbols);
        for (const [symbol, , currency] of currentSymbols) {            
            if (!ws.connected) {
                logger.warn(`Connection lost while processing ${symbol}`);
                break;
            }

            try {
                // Small delay between requests
                await new Promise(resolve => setTimeout(resolve, 200));

                const [success, chartId, seriesKey] = await ws.setupSymbol(symbol, currentTf, currency);
                if (success) {
                    // Store chart info
                    ws.activeCharts[`${symbol}:${currentTf}`] = [chartId, seriesKey, currentTf];
                    logger.debug(`Stored chart info for ${symbol}:${currentTf}`);
                } else {
                    logger.error(`Failed to setup ${symbol} (${currentTf}m)`);
                }
            } catch (error) {
                logger.error(`Error setting up ${symbol}: ${error.message}`);
                continue;
            }
        }

        if (ws.connected) {
            const delay = ws.bars > 5000 ? 10000 : 4000; // 10s for large data, 4s for small
            logger.info(`Completed timeframe ${currentTf}m, waiting ${delay / 1000}s...`);
            await new Promise(resolve => setTimeout(resolve, delay));
        }
    }
}

/**
 * Handle save completion
 */
async function handleSaveCompletion(savePromise, symbol, timeframe) {
    try {
        const saveSuccess = await savePromise;
        if (saveSuccess) {
            ws.completedSymbols.add(`${symbol}:${timeframe}`);
            const completed = ws.completedSymbols.size;
            const total = ws.totalSymbols;
            const remaining = total - completed;

            logger.info(`✅ Saved ${symbol} ${timeframe}m | Progress: ${completed}/${total} (Remaining: ${remaining})`);

            // Check if all pairs completed
                if (total > 0 && completed >= total) {
                logger.info(`🎉 All ${completed}/${total} symbol-timeframe pairs processed!`);
                helper.saveHistoricalConfigLastTime(moment.utc().format('YYYY-MM-DD HH:mm:ss'));
                // Use the implemented close(graceful) method on the WebSocket instance
                await ws.close(true);
            } else if (total === 0) {
                logger.warn('No symbols to process. Shutting down...');
                await ws.close(true);
            }
        } else {
            logger.error(`❌ Error saving data ${symbol} ${timeframe}m`);
        }
    } catch (error) {
        logger.error(`Error handling save completion for ${symbol} ${timeframe}m: ${error.message}`);
    }
}

/**
 * Save data with retry logic
 */
async function saveWithRetrySimple(data, symbol, timeframe) {
    const maxRetries = 3;
    const retryDelay = 2;

    for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
            const success = await trading_service.handleOlvcHistoricalToDbSession(data, symbol, timeframe);

            if (success) {
                return true;
            } else {
                if (attempt < maxRetries - 1) {
                    logger.warn(`Retry ${attempt + 1}/${maxRetries} for ${symbol} ${timeframe}m`);
                    await new Promise(resolve => setTimeout(resolve, retryDelay * 1000 * (attempt + 1)));
                    continue;
                } else {
                    logger.error(`Failed after ${maxRetries} attempts: ${symbol} ${timeframe}m`);
                    return false;
                }
            }
        } catch (error) {
            logger.error(`Error on attempt ${attempt + 1} for ${symbol} ${timeframe}m: ${error.message}`);
            if (attempt < maxRetries - 1) {
                await new Promise(resolve => setTimeout(resolve, retryDelay * 1000 * (attempt + 1)));
            } else {
                return false;
            }
        }
    }

    return false;
}

/**
 * Main function
 */
async function main() {
    try {
        // Set console title
        helper.setConsoleTitle('TradingView Historical - Node.js');

        logger.info('Starting TradingView Historical Data Collector...');

        // Initialize WebSocket instance
        ws = new TradingViewWebSocket();
        ws.completedSymbols = new Set();

        // Load historical config
        const historicalConfig = helper.loadHistoricalConfig();
        const lastTimeHistoricalConfig = historicalConfig.last_time;

        // Calculate required bars
        ws.bars = helper.calculateBarsNeeded(lastTimeHistoricalConfig);
        logger.info(`Calculated bars needed: ${ws.bars}`);

        // Load authentication token
        //await loadAuthToken();
        const isPackaged = process.pkg !== undefined;
        const csvPath = isPackaged
            ? path.join(path.dirname(process.execPath), '..', 'gen_token', 'websocket_tokens.csv')
            : path.join(__dirname, 'nodeapp', 'websocket_tokens.csv');
        logger.info(`📂 Tìm CSV tại: ${csvPath}`);
        if (!fs.existsSync(csvPath)) {
            throw new Error(`Không tìm thấy CSV: ${csvPath}`);
        }
        const results = await new Promise((resolve, reject) => {
            const data = [];
            fs.createReadStream(csvPath)
                .pipe(csvParser())
                .on('data', row => data.push(row))
                .on('end', () => resolve(data))
                .on('error', reject);
        });

        if (!results.length || !results[results.length - 1].token) {
            throw new Error('Định dạng CSV không hợp lệ');
        }
        const authToken = results[results.length - 1].token;
        
        ws.storeAuthToken(authToken);

        // Get timeframes from database
        const rawTimeframe = await trading_service.getTimeframeList();
        TIMEFRAMES = {};
        rawTimeframe.forEach(([tfType, tfCall, seconds]) => {
            const tfMinutes = tfCall && tfCall.match(/^\d+$/) ? parseInt(tfCall) : Math.floor(seconds / 60);
            TIMEFRAMES[tfCall] = [tfType, tfMinutes];
        });
        logger.info(`Timeframes: ${JSON.stringify(TIMEFRAMES)}`);

        // Get symbols from database
        rawSymbols = await trading_service.getAssetsList();
        const validSymbols = rawSymbols
            .map(([symbol]) => symbol)
            .filter(symbol => symbol)
            .map(symbol => symbol.replace(/&/g, 'AND'));

        if (!validSymbols.length) {
            logger.error('No valid symbols retrieved from database');
            return;
        }
        
        // Create configured symbols list
        configuredSymbols = [];
        validSymbols.forEach(symbol => {
            const parts = symbol.split(':');
            if (parts.length >= 4) { // Ensure we have provider:symbol:asset_id:provider_id format
                const baseSymbol = `${parts[0]}:${parts[1]}`;
                const currency = parts[4] || '';
                Object.keys(TIMEFRAMES).forEach(tfCall => {                    
                    configuredSymbols.push([baseSymbol, tfCall, currency]);
                });
            }
        });

        logger.info(`Total symbols from DB: ${configuredSymbols.length}`);
        logger.info(`Configured symbols: ${JSON.stringify(configuredSymbols.slice(0, 5))}...`);

        // Filter timeframes using historical_config.last_time (UTC-based)
        let filteredConfiguredSymbols = configuredSymbols;
        try {
            const result = filterTimeframesByLastTime(configuredSymbols, TIMEFRAMES, lastTimeHistoricalConfig);
            filteredConfiguredSymbols = result.filteredConfiguredSymbols;
            logger.info(`Selected ${result.selectedTfCalls.size} timeframe(s) for this run. Symbol-timeframe pairs after filter: ${filteredConfiguredSymbols.length}`);
            // Store only filtered list to reduce setup work
            ws.storeSymbols(filteredConfiguredSymbols);
        } catch (e) {
            logger.error(`Error filtering timeframes by last_time: ${e.message}`);
            // fallback to store all
            ws.storeSymbols(configuredSymbols);
        }

        // Connect to WebSocket
        if (!await ws.connect()) {
            logger.error('Failed to connect to WebSocket');
            process.exit(1);
        }

        // Send authentication token
        await ws.sendAuthToken(authToken);

        // Start setup process
        const timeframeTask = processSetup(ws, filteredConfiguredSymbols);

        let lastMonitorTime = Date.now() / 1000;

        // Set message handler
        ws.setMessageHandler(async (data) => {
            try {
                // Convert Buffer to string if needed
                const dataStr = data instanceof Buffer ? data.toString('utf8') : String(data);
                if (!dataStr || !dataStr.trim()) return;

                // Handle heartbeat
                if (dataStr.includes('~h~')) {
                    const heartbeatMsg = ws.extractHeartbeat(dataStr);
                    if (heartbeatMsg) {
                        logger.info(`Heartbeat received: ${heartbeatMsg}`);
                        await ws.send(heartbeatMsg);
                        logger.info(`Echoed heartbeat: ${heartbeatMsg}`);
                    }
                }

                // Parse messages
                const messages = ws._parseMessages(dataStr);

                for (const message of messages) {
                    try {
                        if (!message || !message.trim()) continue;
                        if (!message.startsWith('{') && !message.startsWith('[')) continue;

                        const msgData = JSON.parse(message);
                        const messageType = msgData.m;
                        const chartId = msgData.p ? msgData.p[0] : null;

                        // Find symbol and timeframe for this chart
                        let symbol = 'Unknown';
                        let timeframe = 'Unknown';

                        for (const [key, [cId, , tf]] of Object.entries(ws.activeCharts)) {
                            if (cId === chartId) {
                                const keyParts = key.split(':');
                                symbol = `${keyParts[0]}:${keyParts[1]}`;
                                timeframe = keyParts[2];
                                break;
                            }
                        }

                        if (messageType === 'timescale_update') {                          
                            const processedData = ws.processRawData(msgData, symbol, timeframe, chartId, 'HISTORICAL');                            
                            if (processedData && processedData.length > 0) {
                                // Convert to DataFrame-like structure
                                let df = processedData.map(item => ({
                                    'provider:symbol': item['provider:symbol'],
                                    timeframe: timeframe,
                                    timestamp: item.timestamp,
                                    open: item.open,
                                    high: item.high,
                                    low: item.low,
                                    close: item.close,
                                    volume: item.volume
                                }));

                                try {
                                    const baseSymbol = `${symbol.split(':')[0]}:${symbol.split(':')[1]}`;
                                    const matchingRaw = rawSymbols.find(rs => rs[0] && rs[0].startsWith(baseSymbol));

                                    if (!matchingRaw) {
                                        logger.warn(`Không tìm thấy thông tin chi tiết cho symbol: ${baseSymbol}`);
                                        return;
                                    }

                                    const parts = matchingRaw[0].split(':');
                                    if (parts.length >= 4) {
                                        const providerCode = parts[0];
                                        let symbolName = parts[1];
                                        const assetId = parts[2];
                                        const providerId = parts[3];
                                        symbolName = symbolName.replace(/&/g, 'AND');

                                        // Map to fields expected by the stored procedure:
                                        // - symbol: underlying asset symbol (string)
                                        // - provider_code: provider code (string)
                                        // - timeframe_type: code like M1/H1/D (NOT numeric minutes)
                                        df.forEach(row => {
                                            row.symbol = symbolName; // e.g., BTCUSD
                                            row.timeframe_type = TIMEFRAMES[timeframe] ? TIMEFRAMES[timeframe][0] : 'M1';
                                            row.provider_code = providerCode; // e.g., BINANCE
                                            row.date_time = row.timestamp;
                                            // keep OHLCV as is
                                            row.open = row.open;
                                            row.high = row.high;
                                            row.low = row.low;
                                            row.close = row.close;
                                            row.volume = row.volume;
                                        });
                                    } else {
                                        logger.warn(`Định dạng raw_symbol không hợp lệ: ${matchingRaw[0]}`);
                                        return;
                                    }
                                } catch (error) {
                                    logger.error(`Lỗi khi xử lý thông tin symbol: ${error.message}`);
                                    return;
                                }                               

                                // Reorder columns
                                df = df.map(row => ({
                                    symbol: row.symbol,
                                    timeframe_type: row.timeframe_type,
                                    provider_code: row.provider_code,
                                    date_time: row.date_time,
                                    open: row.open,
                                    high: row.high,
                                    low: row.low,
                                    close: row.close,
                                    volume: row.volume
                                }));

                                logger.info(`Processed ${df.length} records for ${symbol} ${timeframe}m`);

                                if (df.length > 50) {
                                    // Remove last candle (similar to Python df.iloc[:-1])
                                    df = df.slice(0, -1);

                                    logger.info(`[HISTORICAL] OHLCV Data - ${symbol} (Timeframe: ${timeframe}m) - ${df.length} records`);
                                    // Display last 3 rows
                                    const lastThreeRows = df.slice(-3);
                                    lastThreeRows.forEach(row => {
                                        logger.info(`${row.symbol} ${row.timeframe_type} ${row.date_time} O:${row.open} H:${row.high} L:${row.low} C:${row.close} V:${row.volume}`);
                                    });
                                    console.log('='.repeat(80));

                                    // Save data asynchronously
                                    const saveTask = saveWithRetrySimple(df, symbol, timeframe);
                                    // Handle completion without blocking
                                    saveTask.then(success => {
                                        handleSaveCompletion(Promise.resolve(success), symbol, timeframe);
                                    }).catch(error => {
                                        logger.error(`Save task error for ${symbol} ${timeframe}m: ${error.message}`);
                                    });
                                }
                            }
                        }

                        // Monitor connection
                        const currentTime = Date.now() / 1000;
                        if (currentTime - lastMonitorTime > 10) {
                            logger.info('WebSocket still active');
                            lastMonitorTime = currentTime;
                        }

                    } catch (error) {
                        logger.error(`Error parsing message: ${error.message}`, error);
                        continue;
                    }
                }
            } catch (error) {
                logger.error(`Error in message handler: ${error.message}`, error);
            }
        });

        // Wait for setup to complete
        await timeframeTask;

        // Keep running until all data is saved
        while (ws.keepRunning) {
            if (!ws.connected) {
                logger.error('WebSocket connection lost, exiting main loop');
                break;
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
        }

    } catch (error) {
        logger.error(`Error in main function: ${error.message}`, error);
    } finally {
        if (ws) {
            await ws.close();
        }
        logger.info('TradingView Historical Data Collector stopped');
        process.exit(0);
    }
}

// Handle graceful shutdown
process.on('SIGINT', async () => {
    logger.info('Received SIGINT, shutting down gracefully...');
    if (ws) {
        await ws.close();
    }
    process.exit(0);
});

process.on('SIGTERM', async () => {
    logger.info('Received SIGTERM, shutting down gracefully...');
    if (ws) {
        await ws.close();
    }
    process.exit(0);
});

// Run main function
if (require.main === module) {
    main().catch((error) => {
        logger.error(`Unhandled error: ${error.message}`, error);
        process.exit(1);
    });
}

module.exports = { main };
