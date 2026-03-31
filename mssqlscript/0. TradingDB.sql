USE master;
GO

-- Create database if it doesn't exist
IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = 'TradingDB')
BEGIN
    CREATE DATABASE TradingDB;
    PRINT N'Created database TradingDB';
END
GO

USE TradingDB;
GO

SET NOCOUNT ON;

-- Create or alter assets table to include refName
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'assets')
BEGIN
    CREATE TABLE assets (
        asset_id INT NOT NULL PRIMARY KEY,  -- Không dùng IDENTITY để có thể edit
        symbol VARCHAR(50) NOT NULL,
        refName VARCHAR(100) NULL,  -- Added column for descriptive name
        [type] VARCHAR(10) NOT NULL,  -- e.g., 'CRYPTO', 'INDICE', 'METAL'
        [timezone] VARCHAR(50) NULL,     -- e.g., 'UTC', 'America/New_York'
        [currency] VARCHAR(50) NULL,    -- e.g., 'JPY', 'USD'
        isActive BIT NOT NULL DEFAULT 1,  -- Default to True
        [provider] VARCHAR(100) NULL,  -- e.g., 'Capital.com'
        [broker] VARCHAR(100) NULL     -- e.g., 'Interactive Brokers', 'Oanda'
    );
    
    -- Tạo sequence để generate asset_id
    IF NOT EXISTS (SELECT * FROM sys.sequences WHERE name = 'seq_asset_id')
    BEGIN
        CREATE SEQUENCE seq_asset_id
        START WITH 1
        INCREMENT BY 1
        NO CACHE;
    END
    
    CREATE NONCLUSTERED INDEX IDX_assets_symbol ON assets (symbol);  -- Index for fast lookup
    PRINT N'Created table assets with refName and index';
END
ELSE IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('assets') AND name = 'refName')
BEGIN
    -- Add refName column if it doesn't exist
    ALTER TABLE assets ADD refName VARCHAR(100) NULL;
    PRINT N'Added refName column to existing assets table';
END

-- Create schema for TVC
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'tvc')
BEGIN
    EXEC('CREATE SCHEMA tvc');
    PRINT N'Created schema tvc';
END

-- Create timeframe table (master for 18 timeframes including M45) with isActive
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'timeframe')
BEGIN
    CREATE TABLE timeframe (
        timeframe_id INT IDENTITY(1,1) PRIMARY KEY,
        timeframe_type VARCHAR(10) NOT NULL UNIQUE,
        timeframe_call VARCHAR(10) NOT NULL UNIQUE,
        seconds INT NOT NULL,
        isActive BIT NOT NULL DEFAULT 1  -- Default to True
    );
    INSERT INTO timeframe (timeframe_type, timeframe_call, seconds, isActive) VALUES
        ('M1', '1', 60, 1), ('M2', '2', 120, 1), ('M3', '3', 180, 1), ('M4', '4', 240, 1), ('M5', '5', 300, 1),
        ('M15', '15', 900, 1), ('M20', '20', 1200, 1), ('M30', '30', 1800, 1), ('M45', '45', 2700, 1), 
        ('M90', '90', 5400, 1),
        ('H1', '60', 3600, 1), ('H2', '120', 7200, 1), ('H3', '180', 10800, 1), ('H4', '240', 14400, 1), ('H6', '360', 21600, 1),
        ('D', 'D', 86400, 1), ('W', 'W', 604800, 1), ('MN', 'M', 2592000, 1);
    PRINT N'Created table timeframe with M45 included';
END

-- Create providers table
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'providers')
BEGIN
    CREATE TABLE providers (
        provider_id INT IDENTITY(1,1) PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        provider_code VARCHAR(50) NOT NULL,
        [platforms] VARCHAR(100) NOT NULL
    );
    PRINT N'Created table providers';
END

-- Create trigger on assets to create timeframe tables in TVC schema
IF NOT EXISTS (SELECT * FROM sys.triggers WHERE name = 'trg_CreateTimeframes_Assets')
BEGIN
    DECLARE @sql NVARCHAR(MAX) = N'
    CREATE TRIGGER trg_CreateTimeframes_Assets
    ON assets
    AFTER INSERT
    AS
    BEGIN
        SET NOCOUNT ON;
        DECLARE @timeframes TABLE (tf VARCHAR(10));
        INSERT INTO @timeframes (tf) VALUES
            (''m1''), (''m2''), (''m3''), (''m4''), (''m5''),
            (''m15''), (''m20''), (''m30''), (''m45''),
            (''m90''),
            (''h1''), (''h2''), (''h3''), (''h4''), (''h6''),
            (''d''), (''w''), (''mn'');

        DECLARE @tf VARCHAR(10);
        DECLARE cur CURSOR LOCAL FAST_FORWARD FOR SELECT tf FROM @timeframes;
        OPEN cur;
        FETCH NEXT FROM cur INTO @tf;
        WHILE @@FETCH_STATUS = 0
        BEGIN
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = @tf AND schema_id = SCHEMA_ID(''tvc''))
            BEGIN
                DECLARE @sqlCreate NVARCHAR(MAX);
                SET @sqlCreate = N''
                CREATE TABLE tvc.'' + QUOTENAME(@tf) + N'' (
                    asset_id INT NOT NULL,
                    timeframe_id INT NOT NULL,
                    provider_id INT NOT NULL,
                    date_time DATETIME2 NOT NULL,
                    [open] FLOAT,
                    [high] FLOAT,
                    [low] FLOAT,
                    [close] FLOAT,
                    [volume] FLOAT,
                    CONSTRAINT PK_tvc_'' + @tf + N'' PRIMARY KEY NONCLUSTERED (asset_id, timeframe_id, provider_id, date_time)
                ) ON [PRIMARY];
                '';
                EXEC sp_executesql @sqlCreate;

                SET @sqlCreate = N''
                CREATE CLUSTERED INDEX IDX_tvc_'' + @tf + N''_Date ON tvc.'' + QUOTENAME(@tf) + N'' (date_time);
                '';
                EXEC sp_executesql @sqlCreate;

                SET @sqlCreate = N''
                CREATE NONCLUSTERED INDEX IDX_tvc_'' + @tf + N''_AssetTFDate ON tvc.'' + QUOTENAME(@tf) + N'' (asset_id, timeframe_id, provider_id, date_time) INCLUDE ([open], [high], [low], [close], [volume]);
                '';
                EXEC sp_executesql @sqlCreate;

                SET @sqlCreate = N''
                CREATE NONCLUSTERED INDEX IDX_tvc_'' + @tf + N''_Close ON tvc.'' + QUOTENAME(@tf) + N'' ([close]) INCLUDE (date_time, asset_id, timeframe_id, provider_id);
                '';
                EXEC sp_executesql @sqlCreate;

                SET @sqlCreate = N''
                CREATE NONCLUSTERED INDEX IDX_tvc_'' + @tf + N''_Volume ON tvc.'' + QUOTENAME(@tf) + N'' ([volume]) INCLUDE (date_time, asset_id, timeframe_id, provider_id);
                '';
                EXEC sp_executesql @sqlCreate;

                SET @sqlCreate = N''
                ALTER TABLE tvc.'' + QUOTENAME(@tf) + N'' REBUILD WITH (DATA_COMPRESSION = PAGE);
                '';
                EXEC sp_executesql @sqlCreate;

                SET @sqlCreate = N''
                ALTER TABLE tvc.'' + QUOTENAME(@tf) + N'' ADD CONSTRAINT FK_tvc_'' + @tf + N''_Asset FOREIGN KEY (asset_id) REFERENCES assets(asset_id);
                ALTER TABLE tvc.'' + QUOTENAME(@tf) + N'' ADD CONSTRAINT FK_tvc_'' + @tf + N''_Timeframe FOREIGN KEY (timeframe_id) REFERENCES timeframe(timeframe_id);
                ALTER TABLE tvc.'' + QUOTENAME(@tf) + N'' ADD CONSTRAINT FK_tvc_'' + @tf + N''_Provider FOREIGN KEY (provider_id) REFERENCES providers(provider_id);
                '';
                EXEC sp_executesql @sqlCreate;

                PRINT N''Created table tvc.'' + @tf + N'' with indexes and compression'';
            END
            FETCH NEXT FROM cur INTO @tf;
        END
        CLOSE cur;
        DEALLOCATE cur;
    END;';
    EXEC sp_executesql @sql;
    PRINT N'Created trigger trg_CreateTimeframes_Assets with M45 support';
END

-- Ensure AUTO_UPDATE_STATISTICS is ON
ALTER DATABASE TradingDB SET AUTO_UPDATE_STATISTICS ON;
PRINT N'Enabled AUTO_UPDATE_STATISTICS for TradingDB';
GO

-- Insert providers
INSERT INTO providers (name, provider_code, platforms) VALUES 
    ('Capital.com', 'CAPITALCOM', 'TVC'),
    ('Binance', 'BINANCE', 'TVC'),
    ('Okx', 'OKX', 'TVC'),
    ('OANDA', 'OANDA', 'TVC'),
    ('FTMO', 'FTMO', 'MT5'),
    ('FTMO', 'FTMO', 'CTRADER');

-- Insert assets 
INSERT INTO assets (asset_id, symbol, refName, [type], timezone, [currency] , isActive, [provider], [broker])
VALUES 
    (56, 'GOLD', 'XAUUSD', 'METAL', 'UTC', 'USD', 1, 'CAPITALCOM', NULL),
    (81, 'BTCUSD', 'BTC/USD', 'CRYPTO', 'UTC','USD', 1, 'CAPITALCOM', NULL),
    (1, 'AU200AU', 'AUS200', 'INDICE', 'UTC','AUD', 1, 'CAPITALCOM', NULL),
    (2, 'FR40', 'FRA40', 'INDICE', 'UTC','EUR', 1, 'CAPITALCOM', NULL),
    (3, 'DE40', 'GER40', 'INDICE', 'UTC','EUR', 1, 'CAPITALCOM', NULL),
    (4, 'HK50', 'HK50', 'INDICE', 'UTC','HKD', 1, 'CAPITALCOM', NULL),
    (5, 'J225', 'JP225', 'INDICE', 'UTC','JPY', 1, 'CAPITALCOM', NULL),
    (7, 'UK100', 'UK100', 'INDICE', 'UTC','GBP', 1, 'CAPITALCOM', NULL),
    (8, 'US500', 'US500', 'INDICE', 'UTC','USD', 1, 'CAPITALCOM', NULL),
    (9, 'US100', 'US100', 'INDICE', 'UTC','USD', 1, 'CAPITALCOM', NULL),
    (10, 'US30', 'US30', 'INDICE', 'UTC','USD', 1, 'CAPITALCOM', NULL),
    (6, 'SP35', 'ES35', 'INDICE', 'UTC','EUR', 1, 'CAPITALCOM', NULL),
    (1001, 'BTCUSD', 'BTCUSD', 'INDICE', 'UTC','USD', 1, 'BINANCE', NULL),
    (1002, 'ETHUSD', 'ETHUSD', 'INDICE', 'UTC','USD', 1, 'OKX', NULL);

PRINT N'Inserted all assets with auto-generated IDs from sequence';

INSERT INTO assets (asset_id, symbol, refName, [type], timezone,[currency],isActive, [provider], [broker])
VALUES (1003, 'ETHUSD', 'ETHUSD', 'INDICE', 'UTC', 'USD', 1, 'BINANCE', NULL)

----------------------------------------------------------
-- Sample query for M2 and M45
USE TradingDB;

DECLARE @asset_id INT;
DECLARE @timeframe_id INT;
DECLARE @provider_id INT;

-- Query for M2 data for ETHUSD from OKX
SELECT @asset_id = asset_id FROM assets WHERE symbol = 'ETHUSD';
SELECT @timeframe_id = timeframe_id FROM timeframe WHERE timeframe_type = 'M2';
SELECT @provider_id = provider_id FROM providers WHERE provider_code = 'OKX';

SELECT 
    date_time,
    [open],
    [high],
    [low],
    [close],
    [volume]
FROM tvc.m2
WHERE 
    asset_id = @asset_id 
    AND timeframe_id = @timeframe_id 
    AND provider_id = @provider_id 
    AND date_time BETWEEN '2022-01-01' AND '2025-12-02'
ORDER BY 
    date_time DESC;

-- Query for M45 data
SELECT @timeframe_id = timeframe_id FROM timeframe WHERE timeframe_type = 'M45';

SELECT 
    date_time,
    [open],
    [high],
    [low],
    [close],
    [volume]
FROM tvc.m45
WHERE 
    asset_id = @asset_id 
    AND timeframe_id = @timeframe_id 
    AND provider_id = @provider_id 
    AND date_time BETWEEN '2022-01-01' AND '2025-12-02'
ORDER BY 
    date_time DESC;

----------------------------------------------------------
-- Delete all data from timeframe tables in TVC schema
DECLARE @timeframe_tables TABLE (
    table_name VARCHAR(20)
);

INSERT INTO @timeframe_tables (table_name)
VALUES
    ('m1'), ('m2'), ('m3'), ('m4'), ('m5'), ('m10'),
    ('m15'), ('m20'), ('m30'), ('m45'),
    ('m90'),
    ('h1'), ('h2'), ('h3'), ('h4'), ('h6'),
    ('d'), ('w'), ('mn');

DECLARE @table_name VARCHAR(20);
DECLARE @sql NVARCHAR(MAX);

-- Use cursor to iterate through each table
DECLARE table_cursor CURSOR FOR
    SELECT table_name FROM @timeframe_tables;

OPEN table_cursor;
FETCH NEXT FROM table_cursor INTO @table_name;

WHILE @@FETCH_STATUS = 0
BEGIN
    IF EXISTS (SELECT * FROM sys.tables WHERE name = @table_name AND schema_id = SCHEMA_ID('tvc'))
    BEGIN
        SET @sql = '
            DELETE FROM tvc.' + @table_name + ';
        ';
        PRINT 'Đang xóa dữ liệu từ bảng: tvc.' + @table_name;
        EXEC sp_executesql @sql;
    END
    ELSE
    BEGIN
        PRINT 'Bảng tvc.' + @table_name + ' không tồn tại, bỏ qua...';
    END

    FETCH NEXT FROM table_cursor INTO @table_name;
END

CLOSE table_cursor;
DEALLOCATE table_cursor;
PRINT 'Đã hoàn tất việc xóa dữ liệu từ schema TVC bao gồm M45.';
GO