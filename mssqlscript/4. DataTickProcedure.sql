
USE TradingDB;
GO

-- Xóa stored procedure InsertBulkTickData trước để giải phóng tham chiếu đến TickDataType
IF OBJECT_ID('InsertBulkTickData', 'P') IS NOT NULL
BEGIN
    DROP PROCEDURE InsertBulkTickData;
    PRINT N'Đã xóa stored procedure InsertBulkTickData.';
END
GO

-- Xóa và tạo lại TickDataType
IF EXISTS (SELECT * FROM sys.types WHERE name = 'TickDataType')
BEGIN
    DROP TYPE TickDataType;
    PRINT N'Đã xóa TickDataType cũ.';
END

CREATE TYPE TickDataType AS TABLE (
    symbol VARCHAR(50) NOT NULL,
    time_msc DATETIME2 NOT NULL,
    bid FLOAT NOT NULL,
    ask FLOAT NOT NULL,
    [last] FLOAT NOT NULL,
    volume FLOAT NOT NULL,
    spread FLOAT NOT NULL,
    [timestamp] BIGINT NOT NULL,
    providers VARCHAR(100) NOT NULL,
    platforms VARCHAR(100) NOT NULL,
    sps VARCHAR(50) NOT NULL
);
PRINT N'Đã tạo TickDataType mới.';
GO

-- Tạo stored procedure để chèn dữ liệu vào data_ticks, bỏ qua bản ghi trùng lặp
CREATE PROCEDURE InsertBulkTickData
    @TickData TickDataType READONLY
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRY
        -- Tạo bảng tạm để lưu dữ liệu
        CREATE TABLE #TempTickData (
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

        -- Chuyển dữ liệu từ @TickData vào bảng tạm
        INSERT INTO #TempTickData (
            symbol, time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
        )
        SELECT 
            symbol,
            time_msc,
            bid,
            ask,
            last,
            volume,
            spread,
            timestamp,
            providers,
            platforms,
            sps
        FROM @TickData;

        -- Kiểm tra dữ liệu không hợp lệ
        IF EXISTS (
            SELECT 1 
            FROM @TickData 
            WHERE symbol IS NULL OR symbol = ''
        )
        BEGIN
            RAISERROR ('Có symbol rỗng hoặc không hợp lệ.', 16, 1);
            IF OBJECT_ID('tempdb..#TempTickData') IS NOT NULL
                DROP TABLE #TempTickData;
            RETURN;
        END

        IF EXISTS (
            SELECT 1 
            FROM @TickData 
            WHERE providers IS NULL OR providers = ''
        )
        BEGIN
            RAISERROR ('Có providers rỗng hoặc không hợp lệ.', 16, 1);
            IF OBJECT_ID('tempdb..#TempTickData') IS NOT NULL
                DROP TABLE #TempTickData;
            RETURN;
        END

        -- Chèn dữ liệu từ bảng tạm vào data_ticks, bỏ qua bản ghi trùng
        INSERT INTO data_ticks (
            symbol, time_msc, bid, ask, last, volume, spread, timestamp, providers, platforms, sps
        )
        SELECT 
            symbol,
            time_msc,
            bid,
            ask,
            last,
            volume,
            spread,
            timestamp,
            providers,
            platforms,
            sps
        FROM #TempTickData t
        WHERE NOT EXISTS (
            SELECT 1 
            FROM data_ticks dt 
            WHERE dt.symbol = t.symbol 
              AND dt.time_msc = t.time_msc 
              AND dt.timestamp = t.timestamp 
              AND dt.providers = t.providers
        );

        -- Cập nhật thống kê để tối ưu hóa hiệu suất truy vấn
        UPDATE STATISTICS data_ticks;

        -- In số lượng bản ghi được chèn
        DECLARE @InsertedRows INT = @@ROWCOUNT;
        PRINT N'Đã chèn ' + CAST(@InsertedRows AS NVARCHAR(10)) + N' bản ghi vào bảng data_ticks.';

        -- Dọn dẹp bảng tạm
        IF OBJECT_ID('tempdb..#TempTickData') IS NOT NULL
            DROP TABLE #TempTickData;

    END TRY
    BEGIN CATCH
        DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
        DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
        DECLARE @ErrorState INT = ERROR_STATE();

        -- Dọn dẹp bảng tạm trước khi báo lỗi
        IF OBJECT_ID('tempdb..#TempTickData') IS NOT NULL
            DROP TABLE #TempTickData;

        -- Bỏ qua lỗi trùng lặp (2627: vi phạm khóa chính)
        IF ERROR_NUMBER() <> 2627
        BEGIN
            RAISERROR (@ErrorMessage, @ErrorSeverity, @ErrorState);
        END
        ELSE
        BEGIN
            -- In thông báo số bản ghi được chèn, bỏ qua lỗi trùng lặp
            DECLARE @InsertedRowsCatch INT = @@ROWCOUNT;
            PRINT N'Đã chèn ' + CAST(@InsertedRowsCatch AS NVARCHAR(10)) + N' bản ghi vào bảng data_ticks (bỏ qua bản ghi trùng lặp).';
        END
    END CATCH
END
GO
