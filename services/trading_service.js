const { sequelize } = require('../config/db_config');
const { DataTypes, QueryTypes } = require('sequelize');
const logger = require('../utils/logger');
const moment = require('moment-timezone');

// Hàm lấy danh sách tài sản (assets)
async function getAssetsList() {
    const query = `
    SELECT
      a.provider + ':' + REPLACE(a.symbol, '&', 'AND') + ':' + CAST(a.asset_id AS NVARCHAR(10)) + ':' + CAST(p.provider_id AS NVARCHAR(10)) + ':' + a.currency AS symbol
    FROM TradingDB.dbo.assets a
    LEFT JOIN TradingDB.dbo.providers p ON a.provider = p.provider_code AND p.platforms = 'TVC'
    WHERE a.isActive = 1 AND a.symbol IS NOT NULL AND p.provider_id IS NOT NULL
    ORDER BY a.type, a.symbol;
  `;

    try {
        const rows = await sequelize.query(query, { type: QueryTypes.SELECT });
        const assets = rows.map(row => row.symbol).filter(symbol => symbol);
        logger.info(`Fetched ${assets.length} active assets`);
        if (!assets.length) {
        logger.warn('No valid assets found in database');
        }
        //console.log('Assets:', assets);
        return assets.map(asset => [asset]);
    } catch (error) {
        logger.error(`Lỗi khi lấy danh sách assets: ${error}`);
        return [];
    }
}

// Hàm lấy danh sách timeframe
async function getTimeframeList() {
    const query = `
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
  `;

    try {
        const rows = await sequelize.query(query, { type: QueryTypes.SELECT });
        const timeframes = rows.map(row => [row.timeframe_type, row.timeframe_call, row.seconds]);
        logger.info(`Fetched ${timeframes.length} active timeframes`);
        return timeframes;
    } catch (error) {
        logger.error(`Lỗi khi lấy danh sách timeframe: ${error}`);
        return [];
    }
}


// Hàm lưu dữ liệu OHLCV historical vào database sử dụng stored procedure
async function handleOlvcHistoricalToDbSession(data, symbol, timeframe) {
    /**
     * Lưu dữ liệu OHLCV vào database bằng stored procedure InsertBulkTimeframeData
     * @param {Array} data - Array of OHLCV data objects
     * @param {string} symbol - Trading symbol
     * @param {number} timeframe - Timeframe in minutes
     * @returns {boolean} Success status
     */

    if (!data || !Array.isArray(data) || data.length === 0) {
        logger.warn(`Empty data array provided for ${symbol}`);
        return false;
    }

    // Pre-validate required properties
    const requiredProps = [
        'symbol',
        'timeframe_type',
        'provider_code',
        'date_time',
        'open',
        'high',
        'low',
        'close',
        'volume'
    ];

    // Check first item to validate structure
    const firstItem = data[0];
    const missingProps = requiredProps.filter(prop => !(prop in firstItem));
    if (missingProps.length > 0) {
        logger.error(`Missing required properties: ${missingProps.join(', ')}`);
        return false;
    }

    // Convert data to records array for stored procedure
    const records = [];
    let numValid = 0;

    for (const item of data) {
        try {
            // Create record object for JSON serialization
            const record = {
                symbol: String(item.symbol || ''),
                timeframe_type: String(item.timeframe_type || ''),
                provider_code: String(item.provider_code || ''),
                date_time: moment.utc(item.date_time, 'YYYY-MM-DD HH:mm:ss,SSS').toISOString(),
                open: parseFloat(item.open) || 0,
                high: parseFloat(item.high) || 0,
                low: parseFloat(item.low) || 0,
                close: parseFloat(item.close) || 0,
                volume: parseFloat(item.volume) || 0
            };

            records.push(record);
            numValid++;
        } catch (error) {
            logger.debug(`Error processing record: ${error.message}`);
            continue;
        }
    }

    if (records.length === 0) {
        logger.warn(`No valid records to insert for ${symbol}`);
        return false;
    }

    logger.info(`Converted ${numValid}/${data.length} valid records for ${symbol}`);

    // Use transaction for database operation
    const transaction = await sequelize.transaction();

    try {
        const startTime = Date.now();

        // Serialize records to JSON for stored procedure
        const jsonData = JSON.stringify(records);
        logger.debug(`Prepared ${records.length} records for SP InsertBulkTimeframeDataJson`);

        // Call stored procedure with JSON parameter
        const [spResult] = await sequelize.query(
            'EXEC TradingDB.dbo.InsertBulkTimeframeDataJson :records',
            {
                replacements: { records: jsonData },
                // Use RAW so we can capture any PRINT/SELECT outputs if the SP returns them
                type: QueryTypes.RAW,
                transaction
            }
        );
        logger.debug(`SP InsertBulkTimeframeDataJson result: ${JSON.stringify(spResult)?.slice(0, 500)}`);

        await transaction.commit();

        const dbTime = (Date.now() - startTime) / 1000;
        logger.info(
            `✅ Successfully inserted ${records.length} records for ${symbol} (timeframe: ${timeframe}m) in ${dbTime.toFixed(2)}s`
        );

        return true;
    } catch (error) {
        await transaction.rollback();
        logger.error(`❌ Database error for ${symbol}: ${error.message}`, error);
        return false;
    }
}


// Export các hàm
module.exports = {
    getAssetsList,
    getTimeframeList,
    handleOlvcHistoricalToDbSession,
};