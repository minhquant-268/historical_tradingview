USE TradingDB;
GO

SET NOCOUNT ON;

-- Thêm timeframe M10 vào bảng timeframe nếu chưa tồn tại
IF NOT EXISTS (SELECT 1 FROM timeframe WHERE timeframe_type = 'M10')
BEGIN
    INSERT INTO timeframe (timeframe_type, timeframe_call, seconds, isActive)
    VALUES ('M10', '10', 600, 1);
    PRINT N'Đã thêm timeframe M10 vào bảng timeframe';
END

-- Cập nhật trigger trg_CreateTimeframes_Assets để bao gồm m10
IF EXISTS (SELECT * FROM sys.triggers WHERE name = 'trg_CreateTimeframes_Assets')
BEGIN
    DROP TRIGGER trg_CreateTimeframes_Assets;
    PRINT N'Đã xóa trigger trg_CreateTimeframes_Assets cũ';
END

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
        (''m10''), (''m15''), (''m20''), (''m30''), (''m45''),
        (''m90''), (''h1''), (''h2''), (''h3''), (''h4''), (''h6''),
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
            CREATE NONCLUSTERED INDEX IDX_tvc_'' + @tf + N''_Add_m10_timeframe.sql ON tvc.'' + QUOTENAME(@tf) + N'' ([close]) INCLUDE (date_time, asset_id, timeframe_id, provider_id);
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

            PRINT N''Đã tạo bảng tvc.'' + @tf + N'' với các index và nén dữ liệu'';
        END
        FETCH NEXT FROM cur INTO @tf;
    END
    CLOSE cur;
    DEALLOCATE cur;
END;';
EXEC sp_executesql @sql;
PRINT N'Đã tạo lại trigger trg_CreateTimeframes_Assets với hỗ trợ m10';

-- Tạo bảng tvc.m10 nếu chưa tồn tại
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'm10' AND schema_id = SCHEMA_ID('tvc'))
BEGIN
    DECLARE @sqlCreate NVARCHAR(MAX);
    SET @sqlCreate = N'
    CREATE TABLE tvc.m10 (
        asset_id INT NOT NULL,
        timeframe_id INT NOT NULL,
        provider_id INT NOT NULL,
        date_time DATETIME2 NOT NULL,
        [open] FLOAT,
        [high] FLOAT,
        [low] FLOAT,
        [close] FLOAT,
        [volume] FLOAT,
        CONSTRAINT PK_tvc_m10 PRIMARY KEY NONCLUSTERED (asset_id, timeframe_id, provider_id, date_time)
    ) ON [PRIMARY];
    ';
    EXEC sp_executesql @sqlCreate;

    SET @sqlCreate = N'
    CREATE CLUSTERED INDEX IDX_tvc_m10_Date ON tvc.m10 (date_time);
    ';
    EXEC sp_executesql @sqlCreate;

    SET @sqlCreate = N'
    CREATE NONCLUSTERED INDEX IDX_tvc_m10_AssetTFDate ON tvc.m10 (asset_id, timeframe_id, provider_id, date_time) INCLUDE ([open], [high], [low], [close], [volume]);
    ';
    EXEC sp_executesql @sqlCreate;

    SET @sqlCreate = N'
    CREATE NONCLUSTERED INDEX IDX_tvc_m10_Close ON tvc.m10 ([close]) INCLUDE (date_time, asset_id, timeframe_id, provider_id);
    ';
    EXEC sp_executesql @sqlCreate;

    SET @sqlCreate = N'
    CREATE NONCLUSTERED INDEX IDX_tvc_m10_Volume ON tvc.m10 ([volume]) INCLUDE (date_time, asset_id, timeframe_id, provider_id);
    ';
    EXEC sp_executesql @sqlCreate;

    SET @sqlCreate = N'
    ALTER TABLE tvc.m10 REBUILD WITH (DATA_COMPRESSION = PAGE);
    ';
    EXEC sp_executesql @sqlCreate;

    SET @sqlCreate = N'
    ALTER TABLE tvc.m10 ADD CONSTRAINT FK_tvc_m10_Asset FOREIGN KEY (asset_id) REFERENCES assets(asset_id);
    ALTER TABLE tvc.m10 ADD CONSTRAINT FK_tvc_m10_Timeframe FOREIGN KEY (timeframe_id) REFERENCES timeframe(timeframe_id);
    ALTER TABLE tvc.m10 ADD CONSTRAINT FK_tvc_m10_Provider FOREIGN KEY (provider_id) REFERENCES providers(provider_id);
    ';
    EXEC sp_executesql @sqlCreate;

    PRINT N'Đã tạo bảng tvc.m10 với các index và nén dữ liệu';
END
GO