# TradingView Historical Data Collector (Node.js)

Node.js version of the TradingView Historical data collection system, converted from Python.

## Project Structure

```
TradingViewHistorical/
├── config/
│   ├── database.js         # Database configuration and connection
│   └── logger.js            # Winston logger setup
├── models/
│   └── TradingViewWebSocket.js  # WebSocket client class
├── services/
│   └── tradingService.js    # Trading data service layer
├── utils/
│   └── helpers.js           # Utility functions
├── main.js                  # Main application entry point
├── package.json             # Node.js dependencies
├── historical_config.json   # Historical data configuration
├── .env.example             # Environment variables template
└── README.md                # This file
```

## Features

- **WebSocket Connection**: Connect to TradingView's real-time data feed
- **Historical Data Collection**: Collect OHLCV historical data for multiple symbols and timeframes
- **Database Integration**: Store data in SQL Server database
- **Retry Logic**: Automatic retry mechanism for failed operations
- **Progress Tracking**: Track completion status of all symbol-timeframe pairs
- **Logging**: Comprehensive logging with Winston
- **Config Management**: JSON-based configuration with auto-cleanup

## Prerequisites

- Node.js >= 18.0.0
- SQL Server database
- TradingView authentication token

## Installation

1. Install dependencies:
```bash
npm install
```

2. Create `.env` file from template:
```bash
cp .env.example .env
```

3. Configure environment variables in `.env`:
```
DB_HOST=localhost
DB_PORT=1433
DB_NAME=TradingDatabase
DB_USER=sa
DB_PASSWORD=your_password
```

## Configuration

### historical_config.json
```json
{
    "last_time": "2025-10-18 01:27:28"
}
```

The `last_time` field is automatically updated after each successful data collection.

## Usage

### Run as Script
```bash
npm start
```

### Build as Executable (Optional)
```bash
npm install -g pkg
pkg . --targets node18-win-x64 --output dist/tradingview-historical.exe
```

## Code Structure

### Main Components

1. **TradingViewWebSocket** (`models/TradingViewWebSocket.js`)
   - Manages WebSocket connection
   - Handles data streaming
   - Processes OHLCV data
   - Tracks completion status

2. **Trading Service** (`services/tradingService.js`)
   - Database operations
   - Get timeframes from database
   - Get assets from database
   - Save OHLCV data with retry logic

3. **Helpers** (`utils/helpers.js`)
   - Message parsing
   - ID generation
   - Config management
   - Date calculations

4. **Logger** (`config/logger.js`)
   - Winston-based logging
   - Console and file output
   - Auto log rotation

## How It Works

1. **Initialization**
   - Load configuration from `historical_config.json`
   - Calculate required bars based on `last_time`
   - Read authentication token from CSV

2. **Data Collection**
   - Fetch timeframes and symbols from database
   - Connect to TradingView WebSocket
   - Setup each symbol-timeframe pair
   - Receive and process historical data
   - Save to database with retry logic

3. **Progress Tracking**
   - Track completed symbol-timeframe pairs
   - Log progress and remaining items
   - Auto-shutdown when all pairs complete

4. **Completion**
   - Update `last_time` in config
   - Close connections gracefully
   - Exit application

## Database Schema

### Required Tables

- `[dbo].[Asset]` - Trading assets/symbols
- `[dbo].[Provider]` - Data providers
- `[dbo].[Timeframe]` - Available timeframes
- `[dbo].[OHLCV]` - OHLCV data storage

## Dependencies

- **ws**: WebSocket client
- **sequelize**: ORM for SQL Server
- **mssql**: SQL Server driver
- **winston**: Logging library
- **dotenv**: Environment variable management
- **moment-timezone**: Date/time handling

## Error Handling

- Automatic retry for failed database operations (max 3 attempts)
- Connection recovery for WebSocket errors
- Transaction rollback on database errors
- Comprehensive error logging

## Logging

Logs are written to:
- Console (colored output)
- `tradingview_historical.log` (file)

Log levels:
- `info`: General information
- `warn`: Warnings
- `error`: Errors with stack traces
- `debug`: Detailed debugging info

## Performance

- Batch processing by timeframe
- Configurable delays between requests
- Connection pooling for database
- Async/await for non-blocking operations

## Troubleshooting

### Connection Issues
- Check `.env` configuration
- Verify SQL Server is running
- Check TradingView token validity

### Data Not Saving
- Check database permissions
- Verify table schema matches
- Review logs for error messages

### Memory Issues
- Reduce number of concurrent symbols
- Increase Node.js heap size: `node --max-old-space-size=4096 main.js`

## License

ISC

## Author

Minh Quach
Huy Le
Nhat Nguyen