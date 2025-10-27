const winston = require('winston');

// Định dạng thời gian UTC với mẫu YYYY-MM-DD HH:mm:ss,SSS
const utcFormat = winston.format((info) => {
    const date = new Date().toISOString().replace('T', ' ').replace('Z', '');
    info.timestamp = date;
    return info;
});

const logger = winston.createLogger({
    level: 'info', // Mức log mặc định
    format: winston.format.combine(
        utcFormat(), // Áp dụng định dạng thời gian UTC
        winston.format.printf(({ level, message, timestamp }) => {
            return `${timestamp} - ${level.toUpperCase()} - ${message}`;
        })
    ),
    transports: [
        new winston.transports.Console({
            // ⚠️ FIX: Tránh blocking console
            handleExceptions: true,
            handleRejections: true,
        }),
        new winston.transports.File({
            filename: 'tradingview_historical.log',
            // ⚠️ FIX: Async file write để không block
            maxsize: 10485760, // 10MB
            maxFiles: 5,
            tailable: true,
            handleExceptions: true,
            handleRejections: true,
            format: winston.format.combine(
                utcFormat(),
                winston.format.printf(({ level, message, timestamp }) => {
                    return `${timestamp} - ${level.toUpperCase()} - ${message}`;
                })
            ),
        }),
        
    ],
});

// ⚠️ FIX: Xử lý lỗi logger để không crash app
logger.on('error', (error) => {
    console.error('Logger error:', error);
});

module.exports = logger;