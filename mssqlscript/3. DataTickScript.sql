
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

-- =====================================================
-- BƯỚC 1: TẠO SCHEMA TICK
-- =====================================================
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'tick')
BEGIN
    EXEC('CREATE SCHEMA tick');
    PRINT N'Đã tạo schema tick';
END
GO

-- =====================================================
-- BƯỚC 2: TẠO BẢNG DATA_TICKS (NHẬN DỮ LIỆU THÔ)
-- =====================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'data_ticks')
BEGIN
    CREATE TABLE data_ticks (
        [symbol] VARCHAR(50) NOT NULL,         
        [time_msc] DATETIME2 NOT NULL,           
        [bid] FLOAT NOT NULL,                        
        [ask] FLOAT NOT NULL,                          
        [last] FLOAT NOT NULL,                        
        [volume] FLOAT NOT NULL,                    
        [spread] FLOAT NOT NULL,                    
        [timestamp] BIGINT NOT NULL,      
        [providers] VARCHAR(100) NOT NULL,   
        [platforms] VARCHAR(100) NOT NULL,
        [sps] VARCHAR(50) NOT NULL,
        CONSTRAINT PK_data_ticks PRIMARY KEY NONCLUSTERED (symbol, time_msc, timestamp, providers)
    ) ON [PRIMARY];

    CREATE CLUSTERED INDEX IDX_data_ticks_Timestamp 
    ON data_ticks (timestamp);

    CREATE NONCLUSTERED INDEX IDX_data_ticks_SymbolProviders 
    ON data_ticks (symbol, providers, timestamp) 
    INCLUDE (bid, ask, last, volume, spread);

    ALTER TABLE data_ticks 
    REBUILD WITH (DATA_COMPRESSION = PAGE);

    PRINT N'Đã tạo bảng data_ticks với indexes và nén dữ liệu trên PRIMARY filegroup';
END
GO

-- =====================================================
-- BƯỚC 3+4: TRIGGER GỘP - TẠO BẢNG VÀ CHUYỂN DỮ LIỆU
-- (Commented-out trigger that deletes data_ticks)
-- =====================================================

CREATE OR ALTER TRIGGER TR_data_ticks_AutoCreateAndTransfer
ON data_ticks
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @Symbol VARCHAR(50);
    DECLARE @TableName NVARCHAR(128);
    DECLARE @SQL NVARCHAR(MAX);
    DECLARE @ErrorMsg NVARCHAR(4000);
    DECLARE @CleanSymbol VARCHAR(50);
    DECLARE @RowCount INT;
    DECLARE @TempTableName NVARCHAR(128);
    
    -- Tạo bảng tạm để lưu snapshot của inserted data (tránh scope issue)
    CREATE TABLE #InsertedSnapshot (
        symbol VARCHAR(50),
        time_msc DATETIME2,
        bid FLOAT,
        ask FLOAT,
        last FLOAT,
        volume FLOAT,
        spread FLOAT,
        timestamp BIGINT,
        providers VARCHAR(100),
        platforms VARCHAR(100),
        sps VARCHAR(50)
    );
    
    -- Copy dữ liệu từ inserted vào bảng tạm (trong trigger context gốc)
    INSERT INTO #InsertedSnapshot
    SELECT 
        symbol, time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
    FROM inserted;
    
    PRINT N'📋 Đã tạo snapshot của ' + CAST(@@ROWCOUNT AS VARCHAR(10)) + ' records từ inserted';
    
    -- Duyệt qua từng symbol DUY NHẤT
    DECLARE symbol_cursor CURSOR LOCAL FAST_FORWARD FOR 
    SELECT DISTINCT symbol FROM #InsertedSnapshot WHERE symbol IS NOT NULL;
    
    OPEN symbol_cursor;
    FETCH NEXT FROM symbol_cursor INTO @Symbol;
    
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @CleanSymbol = REPLACE(@Symbol, '.', '_');
        SET @TableName = QUOTENAME('tick') + '.' + QUOTENAME(@Symbol);
        SET @TempTableName = QUOTENAME('#InsertedSnapshot');
        
        -- BƯỚC 1: KIỂM TRA VÀ TẠO BẢNG NẾU CHƯA CÓ
        IF NOT EXISTS (
            SELECT 1 
            FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = 'tick' AND t.name = @Symbol
        )
        BEGIN
            PRINT N'🏗️  Tạo bảng mới cho symbol: ' + @Symbol;
            
            SET @SQL = '
            CREATE TABLE ' + @TableName + ' (
                [time_msc] DATETIME2 NOT NULL,           
                [bid] FLOAT NOT NULL,                        
                [ask] FLOAT NOT NULL,                          
                [last] FLOAT NOT NULL,                        
                [volume] FLOAT NOT NULL,                    
                [spread] FLOAT NOT NULL,                    
                [timestamp] BIGINT NOT NULL,      
                [providers] VARCHAR(100) NOT NULL,   
                [platforms] VARCHAR(100) NOT NULL,
                [sps] VARCHAR(50) NOT NULL,
                CONSTRAINT PK_' + @CleanSymbol + ' PRIMARY KEY NONCLUSTERED (time_msc, timestamp, providers)
            ) ON [PRIMARY];

            CREATE CLUSTERED INDEX IDX_' + @CleanSymbol + '_Timestamp 
            ON ' + @TableName + ' (timestamp);

            CREATE NONCLUSTERED INDEX IDX_' + @CleanSymbol + '_Providers 
            ON ' + @TableName + ' (providers, timestamp) 
            INCLUDE (bid, ask, last, volume, spread);

            ALTER TABLE ' + @TableName + ' 
            REBUILD WITH (DATA_COMPRESSION = PAGE);';
            
            BEGIN TRY
                EXEC sp_executesql @SQL;
                PRINT N'✅ Đã tạo bảng ' + @TableName + ' thành công';
            END TRY
            BEGIN CATCH
                SET @ErrorMsg = '❌ LỖI khi tạo bảng ' + @TableName + ': ' + ERROR_MESSAGE();
                PRINT @ErrorMsg;
                GOTO NextSymbol;
            END CATCH
        END
        ELSE
        BEGIN
            PRINT N'📂 Bảng ' + @TableName + ' đã tồn tại';
        END
        
        -- BƯỚC 2: CHUYỂN DỮ LIỆU SỬ DỤNG BẢNG TẠM THAY VÌ INSERTED
        IF EXISTS (
            SELECT 1 
            FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = 'tick' AND t.name = @Symbol
        )
        BEGIN
            SET @SQL = '
            INSERT INTO ' + @TableName + ' (time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps)
            SELECT time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
            FROM ' + @TempTableName + ' 
            WHERE symbol = @SymbolParam;';
            
            BEGIN TRY
                EXEC sp_executesql @SQL, N'@SymbolParam VARCHAR(50)', @Symbol;
                SET @RowCount = @@ROWCOUNT;
                PRINT N'➡️  Đã chuyển ' + CAST(@RowCount AS VARCHAR(10)) + ' records sang ' + @TableName;
                
                -- BƯỚC 3: XÓA DỮ LIỆU TỪ DATA_TICKS SỬ DỤNG BẢNG TẠM
                SET @SQL = '
                DELETE dt
                FROM TradingDB.dbo.data_ticks dt
                INNER JOIN ' + @TempTableName + ' temp ON dt.symbol = temp.symbol
                                                   AND dt.time_msc = temp.time_msc
                                                   AND dt.timestamp = temp.timestamp
                                                   AND dt.providers = temp.providers
                WHERE dt.symbol = @SymbolParam;';
                
                EXEC sp_executesql @SQL, N'@SymbolParam VARCHAR(50)', @Symbol;
                DECLARE @DeletedCount INT = @@ROWCOUNT;
                PRINT N'🗑️  Đã xóa ' + CAST(@DeletedCount AS VARCHAR(10)) + ' records tạm khỏi data_ticks cho ' + @Symbol;
                
            END TRY
            BEGIN CATCH
                SET @ErrorMsg = '❌ LỖI khi chuyển/xóa dữ liệu cho ' + @Symbol + ': ' + ERROR_MESSAGE();
                PRINT @ErrorMsg;
                PRINT N'⚠️  Dữ liệu được giữ trong data_ticks để khôi phục thủ công';
            END CATCH
        END
        
    NextSymbol:
        FETCH NEXT FROM symbol_cursor INTO @Symbol;
    END
    
    CLOSE symbol_cursor;
    DEALLOCATE symbol_cursor;
    
    -- Cleanup bảng tạm
    DROP TABLE #InsertedSnapshot;
    
    PRINT N'🎉 Trigger hoàn thành - Batch INSERT ' + CAST(@@ROWCOUNT AS VARCHAR(10)) + ' records thành công';
END
GO


-- =====================================================
-- TRIGGER: TẠO BẢNG + CHUYỂN DỮ LIỆU (KHÔNG XÓA DATA_TICKS)
-- =====================================================
/*
CREATE OR ALTER TRIGGER TR_data_ticks_AutoCreateAndTransfer
ON data_ticks
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @Symbol VARCHAR(50);
    DECLARE @TableName NVARCHAR(128);
    DECLARE @SQL NVARCHAR(MAX);
    DECLARE @ErrorMsg NVARCHAR(4000);
    DECLARE @CleanSymbol VARCHAR(50);
    DECLARE @RowCount INT;
    
    -- Tạo bảng tạm để lưu snapshot của inserted data (tránh scope issue)
    CREATE TABLE #InsertedSnapshot (
        symbol VARCHAR(50),
        time_msc DATETIME2,
        bid FLOAT,
        ask FLOAT,
        last FLOAT,
        volume FLOAT,
        spread FLOAT,
        timestamp BIGINT,
        providers VARCHAR(100),
        platforms VARCHAR(100),
        sps VARCHAR(50)
    );
    
    -- Copy dữ liệu từ inserted vào bảng tạm
    INSERT INTO #InsertedSnapshot
    SELECT 
        symbol, time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
    FROM inserted;
    
    PRINT N'📋 Đã tạo snapshot của ' + CAST(@@ROWCOUNT AS VARCHAR(10)) + ' records từ inserted';
    
    -- Duyệt qua từng symbol DUY NHẤT
    DECLARE symbol_cursor CURSOR LOCAL FAST_FORWARD FOR 
    SELECT DISTINCT symbol FROM #InsertedSnapshot WHERE symbol IS NOT NULL;
    
    OPEN symbol_cursor;
    FETCH NEXT FROM symbol_cursor INTO @Symbol;
    
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @CleanSymbol = REPLACE(@Symbol, '.', '_');
        SET @TableName = QUOTENAME('tick') + '.' + QUOTENAME(@Symbol);
        
        -- BƯỚC 1: KIỂM TRA VÀ TẠO BẢNG NẾU CHƯA CÓ
        IF NOT EXISTS (
            SELECT 1 
            FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = 'tick' AND t.name = @Symbol
        )
        BEGIN
            PRINT N'🏗️  Tạo bảng mới cho symbol: ' + @Symbol;
            
            SET @SQL = '
            CREATE TABLE ' + @TableName + ' (
                [time_msc] DATETIME2 NOT NULL,           
                [bid] FLOAT NOT NULL,                        
                [ask] FLOAT NOT NULL,                          
                [last] FLOAT NOT NULL,                        
                [volume] FLOAT NOT NULL,                    
                [spread] FLOAT NOT NULL,                    
                [timestamp] BIGINT NOT NULL,      
                [providers] VARCHAR(100) NOT NULL,   
                [platforms] VARCHAR(100) NOT NULL,
                [sps] VARCHAR(50) NOT NULL,
                CONSTRAINT PK_' + @CleanSymbol + ' PRIMARY KEY NONCLUSTERED (time_msc, timestamp, providers)
            ) ON [PRIMARY];

            CREATE CLUSTERED INDEX IDX_' + @CleanSymbol + '_Timestamp 
            ON ' + @TableName + ' (timestamp);

            CREATE NONCLUSTERED INDEX IDX_' + @CleanSymbol + '_Providers 
            ON ' + @TableName + ' (providers, timestamp) 
            INCLUDE (bid, ask, last, volume, spread);

            ALTER TABLE ' + @TableName + ' 
            REBUILD WITH (DATA_COMPRESSION = PAGE);';
            
            BEGIN TRY
                EXEC sp_executesql @SQL;
                PRINT N'✅ Đã tạo bảng ' + @TableName + ' thành công';
            END TRY
            BEGIN CATCH
                SET @ErrorMsg = '❌ LỖI khi tạo bảng ' + @TableName + ': ' + ERROR_MESSAGE();
                PRINT @ErrorMsg;
                GOTO NextSymbol;
            END CATCH
        END
        ELSE
        BEGIN
            PRINT N'📂 Bảng ' + @TableName + ' đã tồn tại';
        END
        
        -- BƯỚC 2: CHUYỂN DỮ LIỆU SỬ DỤNG BẢNG TẠM (KHÔNG XÓA)
        SET @SQL = '
        INSERT INTO ' + @TableName + ' (time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps)
        SELECT time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
        FROM #InsertedSnapshot 
        WHERE symbol = @SymbolParam;';
        
        BEGIN TRY
            EXEC sp_executesql @SQL, N'@SymbolParam VARCHAR(50)', @Symbol;
            SET @RowCount = @@ROWCOUNT;
            PRINT N'➡️  Đã chuyển ' + CAST(@RowCount AS VARCHAR(10)) + ' records sang ' + @TableName;
            PRINT N'💾 Data giữ nguyên trong data_ticks làm backup';
        END TRY
        BEGIN CATCH
            SET @ErrorMsg = '❌ LỖI khi chuyển dữ liệu cho ' + @Symbol + ': ' + ERROR_MESSAGE();
            PRINT @ErrorMsg;
            PRINT N'⚠️  Dữ liệu được giữ trong data_ticks để khôi phục thủ công';
        END CATCH
        
    NextSymbol:
        FETCH NEXT FROM symbol_cursor INTO @Symbol;
    END
    
    CLOSE symbol_cursor;
    DEALLOCATE symbol_cursor;
    
    -- Cleanup bảng tạm
    DROP TABLE #InsertedSnapshot;
    
    PRINT N'🎉 Trigger hoàn thành - Batch INSERT ' + CAST(@@ROWCOUNT AS VARCHAR(10)) + ' records';
    PRINT N'📊 Data đã được duplicate: data_ticks (backup) + tick.[Symbol] (production)';
END
GO
*/
-- =====================================================
-- BƯỚC 5: PROCEDURE TẠO BẢNG TICK THỦ CÔNG (KHỦI PHỤC)
-- =====================================================
CREATE OR ALTER PROCEDURE sp_CreateTickTable
    @Symbol VARCHAR(50)
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @TableName NVARCHAR(128);
    DECLARE @SQL NVARCHAR(MAX);
    DECLARE @CleanSymbol VARCHAR(50);
    DECLARE @ErrorMsg NVARCHAR(4000);
    
    IF @Symbol IS NULL OR LTRIM(RTRIM(@Symbol)) = ''
    BEGIN
        RAISERROR('Symbol không được để trống', 16, 1);
        RETURN;
    END
    
    SET @Symbol = LTRIM(RTRIM(@Symbol));
    SET @CleanSymbol = REPLACE(@Symbol, '.', '_');
    SET @TableName = QUOTENAME('tick') + '.' + QUOTENAME(@Symbol);
    
    IF EXISTS (
        SELECT 1 
        FROM sys.tables t
        INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
        WHERE s.name = 'tick' AND t.name = @Symbol
    )
    BEGIN
        PRINT N'Bảng ' + @TableName + ' đã tồn tại';
        RETURN;
    END
    
    SET @SQL = '
    CREATE TABLE ' + @TableName + ' (        
        [time_msc] DATETIME2 NOT NULL,           
        [bid] FLOAT NOT NULL,                        
        [ask] FLOAT NOT NULL,                          
        [last] FLOAT NOT NULL,                        
        [volume] FLOAT NOT NULL,                    
        [spread] FLOAT NOT NULL,                    
        [timestamp] BIGINT NOT NULL,      
        [providers] VARCHAR(100) NOT NULL,   
        [platforms] VARCHAR(100) NOT NULL,
        [sps] VARCHAR(50) NOT NULL,
        CONSTRAINT PK_' + @CleanSymbol + ' PRIMARY KEY NONCLUSTERED (time_msc, timestamp, providers)
    ) ON [PRIMARY];

    CREATE CLUSTERED INDEX IDX_' + @CleanSymbol + '_Timestamp 
    ON ' + @TableName + ' (timestamp);

    CREATE NONCLUSTERED INDEX IDX_' + @CleanSymbol + '_Providers 
    ON ' + @TableName + ' (providers, timestamp) 
    INCLUDE (bid, ask, last, volume, spread);

    ALTER TABLE ' + @TableName + ' 
    REBUILD WITH (DATA_COMPRESSION = PAGE);';
    
    BEGIN TRY
        EXEC sp_executesql @SQL;
        PRINT N'Đã tạo bảng ' + @TableName + ' thành công (thủ công)';
    END TRY
    BEGIN CATCH
        SET @ErrorMsg = 'LỖI khi tạo bảng ' + @TableName + ': ' + ERROR_MESSAGE();
        RAISERROR(@ErrorMsg, 16, 1);
    END CATCH
END
GO

-- =====================================================
-- BƯỚC 6: PROCEDURE CHUYỂN DỮ LIỆU THỦ CÔNG (KHỦI PHỤC)
-- =====================================================
CREATE OR ALTER PROCEDURE sp_TransferDataToTickTable
    @Symbol VARCHAR(50) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @TableName NVARCHAR(128);
    DECLARE @SQL NVARCHAR(MAX);
    DECLARE @RowCount INT;
    DECLARE @CurrentSymbol VARCHAR(50);
    
    IF @Symbol IS NULL
    BEGIN
        DECLARE symbol_cursor CURSOR LOCAL FAST_FORWARD FOR 
        SELECT DISTINCT symbol FROM data_ticks WHERE symbol IS NOT NULL;
        
        OPEN symbol_cursor;
        FETCH NEXT FROM symbol_cursor INTO @CurrentSymbol;
        
        WHILE @@FETCH_STATUS = 0
        BEGIN
            EXEC sp_TransferDataToTickTable @CurrentSymbol;
            FETCH NEXT FROM symbol_cursor INTO @CurrentSymbol;
        END
        
        CLOSE symbol_cursor;
        DEALLOCATE symbol_cursor;
        RETURN;
    END
    
    SET @TableName = QUOTENAME('tick') + '.' + QUOTENAME(@Symbol);
    
    IF NOT EXISTS (
        SELECT 1 
        FROM sys.tables t
        INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
        WHERE s.name = 'tick' AND t.name = @Symbol
    )
    BEGIN
        RAISERROR('Bảng tick.%s chưa tồn tại. Chạy sp_CreateTickTable trước.', 16, 1, @Symbol);
        RETURN;
    END
    
    SET @SQL = '
    INSERT INTO ' + @TableName + ' (time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps)
    SELECT time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
    FROM data_ticks 
    WHERE symbol = @SymbolParam;';
    
    BEGIN TRY
        EXEC sp_executesql @SQL, N'@SymbolParam VARCHAR(50)', @SymbolParam = @Symbol;
        SET @RowCount = @@ROWCOUNT;
        
        DELETE FROM data_ticks WHERE symbol = @Symbol;
        
        PRINT N'Đã chuyển ' + CAST(@RowCount AS VARCHAR(10)) + ' records cho symbol ' + @Symbol;
    END TRY
    BEGIN CATCH
        DECLARE @ErrorMsg NVARCHAR(4000) = 'LỖI khi chuyển dữ liệu cho ' + @Symbol + ': ' + ERROR_MESSAGE();
        RAISERROR(@ErrorMsg, 16, 1);
    END CATCH
END
GO

-- =====================================================
-- BƯỚC 7: VIEW XEM TỔNG QUAN CÁC BẢNG TICK
-- =====================================================
CREATE OR ALTER VIEW vw_TickTablesOverview
AS
SELECT TOP (100) PERCENT
    t.name AS symbol,
    QUOTENAME('tick') + '.' + QUOTENAME(t.name) AS table_name,
    t.create_date,
    SUM(p.rows) AS row_count,
    t.modify_date AS last_updated
FROM sys.tables t
INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
INNER JOIN sys.indexes i ON i.object_id = t.object_id AND i.index_id <= 1
INNER JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id = i.index_id AND p.partition_number = 1
WHERE s.name = 'tick'
GROUP BY t.name, t.create_date, t.modify_date
ORDER BY t.name;
GO

-- =====================================================
-- BƯỚC 8: PROCEDURE LIST CÁC BẢNG TICK
-- =====================================================
CREATE OR ALTER PROCEDURE sp_ListTickTables
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT 
        s.name AS schema_name,
        t.name AS symbol,
        QUOTENAME(s.name) + '.' + QUOTENAME(t.name) AS table_name,
        t.create_date,
        SUM(p.rows) AS row_count,
        t.modify_date AS last_updated
    FROM sys.tables t
    INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
    INNER JOIN sys.indexes i ON i.object_id = t.object_id AND i.index_id <= 1
    INNER JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id = i.index_id
    WHERE s.name = 'tick'
    GROUP BY s.name, t.name, t.create_date, t.modify_date
    ORDER BY t.name;
END
GO

-- =====================================================
-- BƯỚC 9: PROCEDURE KIỂM TRA TÌNH TRẠNG HỆ THỐNG
-- =====================================================
CREATE OR ALTER PROCEDURE sp_CheckSystemStatus
AS
BEGIN
    SET NOCOUNT ON;
    
    PRINT N'=== KIỂM TRA TÌNH TRẠNG HỆ THỐNG - ' + CONVERT(VARCHAR, GETDATE(), 120) + ' ===';
    
    PRINT N'\n1. DỮ LIỆU CHỜ XỬ LÝ TRONG DATA_TICKS:';
    IF EXISTS (SELECT 1 FROM data_ticks WHERE symbol IS NOT NULL)
    BEGIN
        SELECT 
            symbol,
            COUNT(*) as pending_records,
            MIN(time_msc) as oldest_record,
            MAX(time_msc) as newest_record,
            AVG(spread) as avg_spread
        FROM data_ticks 
        WHERE symbol IS NOT NULL
        GROUP BY symbol 
        ORDER BY pending_records DESC;
    END
    ELSE
        PRINT N'  -> Không có dữ liệu chờ xử lý.';
    
    PRINT N'\n2. CÁC BẢNG TICK ĐÃ TỒN TẠI:';
    EXEC sp_ListTickTables;
    
    PRINT N'\n3. SYMBOL CÓ DỮ LIỆU NHƯNG CHƯA CÓ BẢNG:';
    IF EXISTS (
        SELECT 1 FROM data_ticks dt
        WHERE NOT EXISTS (
            SELECT 1 FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = 'tick' AND t.name = dt.symbol
        )
    )
    BEGIN
        SELECT DISTINCT dt.symbol
        FROM data_ticks dt
        WHERE NOT EXISTS (
            SELECT 1 FROM sys.tables t
            INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
            WHERE s.name = 'tick' AND t.name = dt.symbol
        )
        ORDER BY dt.symbol;
    END
    ELSE
        PRINT N'  -> Tất cả symbol đều có bảng.';
END
GO

-- =====================================================
-- BƯỚC 10: PROCEDURE KHỞI PHỤC NHANH
-- =====================================================
CREATE OR ALTER PROCEDURE sp_QuickRecovery
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @Symbols TABLE (symbol VARCHAR(50));
    DECLARE @Symbol VARCHAR(50);
    
    PRINT N'=== BẮT ĐẦU KHỞI PHỤC NHANH ===';
    
    INSERT INTO @Symbols (symbol)
    SELECT DISTINCT dt.symbol
    FROM data_ticks dt
    WHERE dt.symbol IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM sys.tables t
        INNER JOIN sys.schemas s ON t.schema_id = s.schema_id
        WHERE s.name = 'tick' AND t.name = dt.symbol
    );
    
    IF EXISTS (SELECT 1 FROM @Symbols)
    BEGIN
        DECLARE symbol_cursor CURSOR LOCAL FAST_FORWARD FOR SELECT symbol FROM @Symbols;
        
        OPEN symbol_cursor;
        FETCH NEXT FROM symbol_cursor INTO @Symbol;
        
        WHILE @@FETCH_STATUS = 0
        BEGIN
            PRINT N'Tạo bảng cho: ' + @Symbol;
            BEGIN TRY
                EXEC sp_CreateTickTable @Symbol;
            END TRY
            BEGIN CATCH
                PRINT N'LỖI tạo bảng ' + @Symbol + ': ' + ERROR_MESSAGE();
            END CATCH
            FETCH NEXT FROM symbol_cursor INTO @Symbol;
        END
        
        CLOSE symbol_cursor;
        DEALLOCATE symbol_cursor;
    END
    ELSE
        PRINT N'Không có symbol nào cần tạo bảng.';
    
    PRINT N'\nChuyển dữ liệu kẹt...';
    EXEC sp_TransferDataToTickTable;
    
    PRINT N'\n=== KẾT QUẢ SAU KHỞI PHỤC ===';
    EXEC sp_CheckSystemStatus;
    
    PRINT N'=== HOÀN THÀNH KHỞI PHỤC ===';
END
GO

-- =====================================================
-- BƯỚC 11: TEST VÀ KIỂM TRA
-- =====================================================
PRINT N'=== HOÀN THÀNH CÀI ĐẶT HỆ THỐNG ===';
PRINT N'Các thành phần đã được tạo:';
PRINT N'- Schema: tick';
PRINT N'- Table: data_ticks';
PRINT N'- Trigger: TR_data_ticks_AutoCreateAndTransfer';
PRINT N'- Procedures: sp_CreateTickTable, sp_TransferDataToTickTable, sp_ListTickTables, sp_CheckSystemStatus, sp_QuickRecovery';
PRINT N'- View: vw_TickTablesOverview';

PRINT N'\n=== HƯỚNG DẪN TEST ===';
PRINT N'1. Test insert dữ liệu:';
PRINT N'   INSERT INTO data_ticks (...) VALUES (...);';
PRINT N'2. Kiểm tra:';
PRINT N'   EXEC sp_ListTickTables;';
PRINT N'   SELECT * FROM vw_TickTablesOverview;';
PRINT N'3. Xem dữ liệu:';
PRINT N'   SELECT TOP 10 * FROM tick.BTCUSD ORDER BY timestamp DESC;';
PRINT N'4. Kiểm tra hệ thống:';
PRINT N'   EXEC sp_CheckSystemStatus;';
GO

-- =====================================================
-- query nhanh
-- =====================================================
-- Kiểm tra trạng thái hệ thống
EXEC sp_CheckSystemStatus;

-- Xem thống kê theo symbol
SELECT 
    symbol,
    COUNT(*) as backup_records,
    MIN(time_msc) as oldest_backup,
    MAX(time_msc) as newest_backup
FROM data_ticks 
GROUP BY symbol 
ORDER BY backup_records DESC;

-- Chỉ chuyển được khi data ở trong data_tick còn tồn tại
-- Chuyển từng symbol một (an toàn hơn khi debug) => Manual thủ công 
EXEC sp_TransferDataToTickTable 'HK50';
EXEC sp_TransferDataToTickTable 'US30';
EXEC sp_TransferDataToTickTable 'EURUSD';

-- 🔄 Chuyển tất cả (one-click)
EXEC sp_TransferDataToTickTable;

-- 🔍 Chuyển symbol cụ thể
EXEC sp_TransferDataToTickTable 'US500';

-- 🧹 Chuyển và cleanup (nếu muốn xóa backup)
-- EXEC sp_TransferDataToTickTable 'HK50';
-- DELETE FROM data_ticks WHERE symbol = 'HK50';
GO
