USE [TradingDB]
GO
/****** Object:  StoredProcedure [dbo].[InsertBulkTimeframeData]    Script Date: 9/30/2025 8:03:05 PM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

-- Tạo Table Type đơn giản hóa
IF NOT EXISTS (SELECT 1 FROM sys.types WHERE name = 'TimeframeDataType' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TYPE dbo.TimeframeDataType AS TABLE (
        symbol VARCHAR(50),
        timeframe_type VARCHAR(10),
        provider_code VARCHAR(50),
        date_time DATETIME2,
        [open] FLOAT,
        [high] FLOAT,
        [low] FLOAT,
        [close] FLOAT,
        [volume] FLOAT
    );
END
GO

CREATE or ALTER PROCEDURE [dbo].[InsertBulkTimeframeData]
    @TimeframeData dbo.TimeframeDataType READONLY
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    
    BEGIN TRY
        -- ✅ Đơn giản hóa: Insert trực tiếp vào bảng phù hợp
        DECLARE @table_name NVARCHAR(50);
        DECLARE @timeframe_type VARCHAR(10);
        
        -- Lấy timeframe_type từ dữ liệu đầu vào (giả sử tất cả records cùng timeframe)
        SELECT TOP 1 @timeframe_type = timeframe_type FROM @TimeframeData;
        
        -- Map timeframe_type to table name
        SET @table_name = CASE @timeframe_type
            WHEN 'M1' THEN 'm1'
            WHEN 'M2' THEN 'm2'
            WHEN 'M3' THEN 'm3'
            WHEN 'M4' THEN 'm4'
            WHEN 'M5' THEN 'm5' 
            WHEN 'M15' THEN 'm15' 
            WHEN 'M30' THEN 'm30'
            WHEN 'M45' THEN 'm45'
            WHEN 'M90' THEN 'm90'
            WHEN 'H1' THEN 'h1'
            WHEN 'H2' THEN 'h2'
            WHEN 'H3' THEN 'h3'
            WHEN 'H4' THEN 'h4'
            WHEN 'H6' THEN 'h6'
            WHEN 'D' THEN 'd' 
            WHEN 'MN' THEN 'mn'
            WHEN 'W' THEN 'w' ELSE LOWER(@timeframe_type)
        END;
        
        -- Kiểm tra bảng tồn tại
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = @table_name AND schema_id = SCHEMA_ID('tvc'))
        BEGIN
            PRINT N'⚠️ Table not found: ' + @table_name;
            RETURN -1;
        END
        
        -- ✅ Đơn giản hóa MERGE operation
        DECLARE @sql NVARCHAR(MAX) = N'
            MERGE INTO TradingDB.tvc.' + QUOTENAME(@table_name) + N' AS target
            USING (
                SELECT 
                    a.asset_id,
                    t.timeframe_id, 
                    p.provider_id,
                    td.date_time,
                    td.[open],
                    td.[high], 
                    td.[low], 
                    td.[close],
                    td.[volume]
                FROM @TimeframeData td
                INNER JOIN TradingDB.dbo.assets a ON td.symbol = a.symbol AND a.isActive = 1
                INNER JOIN TradingDB.dbo.timeframe t ON td.timeframe_type = t.timeframe_type AND t.isActive = 1
                INNER JOIN TradingDB.dbo.providers p ON td.provider_code = p.provider_code
            ) AS source
            ON target.asset_id = source.asset_id
                AND target.timeframe_id = source.timeframe_id
                AND target.provider_id = source.provider_id
                AND target.date_time = source.date_time
            WHEN NOT MATCHED BY TARGET THEN
                INSERT (asset_id, timeframe_id, provider_id, date_time, [open], [high], [low], [close], [volume])
                VALUES (source.asset_id, source.timeframe_id, source.provider_id, source.date_time, 
                        source.[open], source.[high], source.[low], source.[close], source.[volume]);
        ';
        
        EXEC sp_executesql @sql, N'@TimeframeData dbo.TimeframeDataType READONLY', @TimeframeData;
        
        PRINT N'✅ Inserted data into ' + @table_name;
        RETURN 1;
        
    END TRY
    BEGIN CATCH
        DECLARE @ErrorMsg NVARCHAR(4000) = ERROR_MESSAGE();
        DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
        DECLARE @ErrorState INT = ERROR_STATE();
        
        PRINT N'❌ Error: ' + @ErrorMsg;
        
        -- Re-throw error để Python có thể bắt được
        RAISERROR(@ErrorMsg, @ErrorSeverity, @ErrorState);
        RETURN -1;
    END CATCH
END;