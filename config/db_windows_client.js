const sql = require('mssql/msnodesqlv8');

module.exports = function createClient(cfg = {}) {
    // Read raw values (allow specifying instance or port inside DB_SERVER)
    let rawServer = (cfg.server || process.env.DB_SERVER || '').toString();
    let DB_INSTANCE = cfg.instance || process.env.DB_INSTANCE;
    let DB_NAME = cfg.database || process.env.DB_NAME;
    let DB_PORT = cfg.port || process.env.DB_PORT;

    // Normalize server string: allow formats like
    // - host\INSTANCE  (common)
    // - host/INSTANCE   (some users use / in SSMS)
    // - host,1433       (host with port)
    // - tcp:host,1433
    // If instance is embedded in rawServer and user did not explicitly set DB_INSTANCE,
    // extract it. If port is embedded and DB_PORT not provided, extract it.
    let DB_SERVER = rawServer || undefined;
    if (DB_SERVER) {
        // strip tcp: prefix if present
        DB_SERVER = DB_SERVER.replace(/^tcp:/i, '');

        // detect host\instance or host/instance
        const instMatch = DB_SERVER.match(/^([^\\\/]+)[\\\/]([^,]+)(?:,(\d+))?$/);
        if (instMatch) {
            // host\instance (optionally, with ,port at end)
            DB_SERVER = instMatch[1];
            if (!DB_INSTANCE) DB_INSTANCE = instMatch[2];
            if (!DB_PORT && instMatch[3]) DB_PORT = instMatch[3];
        } else {
            // detect host,port
            const portMatch = DB_SERVER.match(/^([^,]+),(\d+)$/);
            if (portMatch) {
                DB_SERVER = portMatch[1];
                if (!DB_PORT) DB_PORT = portMatch[2];
            }
        }
    }

    // default port fallback
    if (!DB_PORT) DB_PORT = 50123;

    // If connecting to local machine and no instance specified, try to auto-detect
    // a named instance by inspecting Windows services (MSSQL$INSTANCENAME).
    // If a default instance (MSSQLSERVER) exists, we leave DB_INSTANCE undefined
    // because default instance is accessed via 'localhost'.
    async function detectLocalInstance() {
        if (!DB_SERVER) return null;
        const lc = DB_SERVER.toLowerCase();
        if (!(lc === 'localhost' || lc === '127.0.0.1' || lc === '.' || lc === '(local)')) return null;
        if (DB_INSTANCE) return DB_INSTANCE;
        try {
            const { exec } = require('child_process');
            const p = new Promise((resolve) => {
                exec('sc query state= all', { windowsHide: true, timeout: 5000 }, (err, stdout, stderr) => {
                    if (err) {
                        console.warn('[db_windows_client] detectLocalInstance exec error:', err && err.message ? err.message : err);
                        return resolve(null);
                    }
                    if (!stdout) return resolve(null);
                    // debug
                    // console.log('[db_windows_client] detectLocalInstance stdout length', stdout.length);
                    const lines = stdout.split(/\r?\n/);
                    // look for named instance services: SERVICE_NAME: MSSQL$INSTANCENAME
                    for (const ln of lines) {
                        const m = ln.match(/SERVICE_NAME:\s*(MSSQL\$([^\s]+))/i);
                        if (m) {
                            console.log('[db_windows_client] detectLocalInstance found named instance:', m[2]);
                            return resolve(m[2]);
                        }
                    }
                    // if no named instance, but default instance exists, return null (use server-only)
                    const hasDefault = lines.some(l => /SERVICE_NAME:\s*MSSQLSERVER/i.test(l));
                    if (hasDefault) console.log('[db_windows_client] detectLocalInstance default instance present');
                    return resolve(null);
                });
            });
            const found = await p;
            if (found) DB_INSTANCE = found;
            return found;
        } catch (e) {
            console.warn('[db_windows_client] detectLocalInstance caught', e && e.message ? e.message : e);
            return null;
        }
    }

    const poolConfig = {
        server: DB_SERVER,
        database: DB_NAME,
        driver: 'msnodesqlv8',
        port: DB_PORT,
        options: {
            instanceName: DB_INSTANCE || undefined,
            trustServerCertificate: true,
            trustedConnection: true,
            port: DB_PORT
        }
    };

    // If a port is provided and no instance name is used, include port in server
    // (SQL Server accepts 'server,port' syntax). If an instance is provided we
    // keep instanceName (which uses named instance resolution) and ignore port.
    if (DB_PORT && !DB_INSTANCE) {
        // Use host,port form
        poolConfig.server = `${DB_SERVER},${DB_PORT}`;
        // also set numeric port in options in case the driver reads it
        poolConfig.options.port = Number(DB_PORT);
    }

    let pool = null;

    async function tryConnect(cfg) {
        const attemptPool = new sql.ConnectionPool(cfg);
        try {
            console.log('[db_windows_client] tryConnect - connecting with', { server: cfg.server, options: cfg.options });
            // rely on driver-level connection timeout if available, but also guard with a JS timeout
            const connectPromise = attemptPool.connect();
            const result = await Promise.race([
                connectPromise,
                new Promise((_, rej) => setTimeout(() => rej(new Error('CONNECT_TIMEOUT')), 8000))
            ]);
            console.log('[db_windows_client] tryConnect - connected');
            return attemptPool;
        } catch (err) {
            try { await attemptPool.close(); } catch (e) { }
            throw err;
        }
    }

    async function ensurePool() {
        if (pool && pool.connected) return pool;
        // attempt to detect local instance synchronously before trying candidates
        try {
            // detectLocalInstance returns instance name or null
            // call it and wait (it uses child_process.exec)
            // eslint-disable-next-line no-await-in-loop
            await detectLocalInstance();
        } catch (e) {
            // ignore detection errors
        }

        // Update poolConfig with any discovered instance before building candidates
        poolConfig.options = poolConfig.options || {};
        poolConfig.options.instanceName = DB_INSTANCE || undefined;
        // If we discovered an instance, ensure server is the host only (no ",port")
        if (DB_INSTANCE) {
            poolConfig.server = DB_SERVER;
            delete poolConfig.port;
            if (poolConfig.options) delete poolConfig.options.port;
        }

        // Build two candidate configs when instance is not provided:
        // 1) server-only (allow local shared memory / named pipes / instance resolution)
        // 2) server,port (explicit TCP)
        const baseCfg = JSON.parse(JSON.stringify(poolConfig));
        // set small connection timeout hints
        baseCfg.connectionTimeout = baseCfg.connectionTimeout || 8000;
        baseCfg.options = baseCfg.options || {};
        baseCfg.options.connectTimeout = baseCfg.options.connectTimeout || 8000;

        const candidates = [];
        if (!baseCfg.options.instanceName) {
            // try server-only first
            const c1 = JSON.parse(JSON.stringify(baseCfg));
            c1.server = DB_SERVER; // no ",port"
            delete c1.port;
            c1.options = Object.assign({}, c1.options);
            delete c1.options.port;
            candidates.push(c1);

            // then server,port
            const c2 = JSON.parse(JSON.stringify(baseCfg));
            c2.server = `${DB_SERVER},${DB_PORT}`;
            c2.options = Object.assign({}, c2.options);
            c2.options.port = Number(DB_PORT);
            candidates.push(c2);
        } else {
            // If instanceName provided, try that config only
            candidates.push(baseCfg);
        }

        let lastErr = null;
        for (const cfgTry of candidates) {
            try {
                console.log('[db_windows_client] ensurePool - attempting candidate', { server: cfgTry.server, instanceName: cfgTry.options.instanceName, port: cfgTry.options.port });
                pool = await tryConnect(cfgTry);
                return pool;
            } catch (err) {
                console.warn('[db_windows_client] connection attempt failed:', err && err.message ? err.message : err);
                lastErr = err;
            }
        }
        throw lastErr || new Error('Unable to establish DB connection');
    }

    return {
        // mimic the minimal API used by the codebase
        async authenticate() {
            await ensurePool();
        },
        async close() {
            try {
                if (pool) await pool.close();
            } catch (e) { }
        },
        // query(sql, options) — supports options.type === 'SELECT' and options.transaction
        async query(sqlText, options = {}) {
            const p = await ensurePool();
            const useTxn = options && options.transaction;
            const requester = useTxn ? new sql.Request(options.transaction._txn) : p.request();

            // handle replacements like ':records' -> '@records' and bind parameter values
            if (options && options.replacements && typeof options.replacements === 'object') {
                Object.keys(options.replacements).forEach((k) => {
                    try {
                        sqlText = sqlText.replaceAll(':' + k, '@' + k);
                    } catch (e) {
                        const re = new RegExp(':' + k + '\\b', 'g');
                        sqlText = sqlText.replace(re, '@' + k);
                    }
                    // bind as NVARCHAR(MAX) by default
                    try {
                        requester.input(k, sql.NVarChar(sql.MAX), options.replacements[k]);
                    } catch (e) {
                        try { requester.input(k, options.replacements[k]); } catch (e2) { }
                    }
                });
            }

            const result = await requester.query(sqlText);
            const QueryTypes = require('sequelize').QueryTypes;

            if (options && options.type === QueryTypes.SELECT) {
                // Sequelize returns rows array for SELECT
                return result.recordset;
            }

            if (options && options.type === QueryTypes.RAW) {
                // mimic Sequelize: return [results, metadata]
                return [result.recordset, result];
            }

            // default: return result object
            return result;
        },
        // transaction() -> returns object with _txn and commit/rollback
        async transaction() {
            const p = await ensurePool();
            const tx = new sql.Transaction(p);
            await tx.begin();
            return {
                _txn: tx,
                commit: async () => await tx.commit(),
                rollback: async () => await tx.rollback()
            };
        }
    };
};
