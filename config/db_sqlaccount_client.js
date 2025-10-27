// SQL Account client factory using Sequelize + tedious
const { Sequelize } = require('sequelize');
const tedious = require('tedious');

// This factory returns a lightweight proxy that will try a small set of
// connection configurations (server-only, server,port, or named-instance)
// and resolve to a real Sequelize instance once authenticate() succeeds.
module.exports = function createClient(cfg = {}) {
    const rawServer = cfg.server || process.env.DB_SERVER;
    let DB_SERVER = rawServer;
    let DB_INSTANCE = cfg.instance || process.env.DB_INSTANCE;
    const DB_NAME = cfg.database || process.env.DB_NAME;
    const DB_USER = cfg.user || process.env.DB_USER;
    const DB_PASSWORD = cfg.password || process.env.DB_PASSWORD;
    let DB_PORT = cfg.port || process.env.DB_PORT;
    if (!DB_PORT) DB_PORT = 50123;

    // normalize rawServer: allow forms host\instance, host/instance, host,port, tcp:host,port
    if (DB_SERVER && typeof DB_SERVER === 'string') {
        DB_SERVER = DB_SERVER.replace(/^tcp:/i, '');
        const instMatch = DB_SERVER.match(/^([^\\\/]+)[\\\/]([^,]+)(?:,(\d+))?$/);
        if (instMatch) {
            DB_SERVER = instMatch[1];
            if (!DB_INSTANCE) DB_INSTANCE = instMatch[2];
            if (!DB_PORT && instMatch[3]) DB_PORT = instMatch[3];
        } else {
            const portMatch = DB_SERVER.match(/^([^,]+),(\d+)$/);
            if (portMatch) {
                DB_SERVER = portMatch[1];
                if (!DB_PORT) DB_PORT = portMatch[2];
            }
        }
    }

    // Builds a Sequelize instance for given options
    function buildSequelize(opts) {
        return new Sequelize(DB_NAME, DB_USER, DB_PASSWORD, {
            dialect: 'mssql',
            dialectModule: tedious,
            host: opts.host,
            port: opts.port,
            dialectOptions: {
                options: Object.assign({
                    encrypt: false,
                    trustServerCertificate: true
                }, opts.dialectOptions && opts.dialectOptions.options ? opts.dialectOptions.options : {})
            },
            pool: {
                max: 50,
                min: 0,
                acquire: 30000,
                idle: 10000,
            },
            logging: false,
        });
    }

    // Candidate configurations to try in order
    const candidates = [];
    // If DB_SERVER looks like localhost and DB_INSTANCE is not provided,
    // try to detect a local named instance via `sc query` so behaviour matches SSMS.
    async function detectLocalInstance() {
        if (!DB_SERVER) return null;
        const lc = DB_SERVER.toString().toLowerCase();
        if (!(lc === 'localhost' || lc === '127.0.0.1' || lc === '.' || lc === '(local)')) return null;
        if (DB_INSTANCE) return DB_INSTANCE;
        try {
            const { exec } = require('child_process');
            const found = await new Promise((resolve) => {
                exec('sc query state= all', { windowsHide: true, timeout: 5000 }, (err, stdout) => {
                    if (err || !stdout) return resolve(null);
                    const lines = stdout.split(/\r?\n/);
                    for (const ln of lines) {
                        const m = ln.match(/SERVICE_NAME:\s*(MSSQL\$([^\s]+))/i);
                        if (m) return resolve(m[2]);
                    }
                    // if default instance only, return null
                    return resolve(null);
                });
            });
            if (found) DB_INSTANCE = found;
            return found;
        } catch (e) {
            return null;
        }
    }
    // Candidate generation will be performed inside ensureReal so detection
    // (detectLocalInstance) completes before we try configs.

    // proxy object returned immediately. It will lazily connect when authenticate() is called
    let real = null;

    async function tryAuthenticateWith(sequelizeInstance, timeoutMs = 10000) {
        // race authenticate with a timeout to avoid long hangs
        return Promise.race([
            sequelizeInstance.authenticate(),
            new Promise((_, rej) => setTimeout(() => rej(new Error('AUTH_TIMEOUT')), timeoutMs))
        ]);
    }

    async function ensureReal() {
        if (real) return real;
        // run detection for local instance if applicable
        await detectLocalInstance().catch(() => null);

        // rebuild candidates now that DB_INSTANCE may have been populated
        const localCandidates = [];
        if (DB_INSTANCE) {
            localCandidates.push({ host: DB_SERVER, port: undefined, dialectOptions: { options: { instanceName: DB_INSTANCE } } });
        } else {
            localCandidates.push({ host: DB_SERVER, port: undefined, dialectOptions: { options: {} } });
            localCandidates.push({ host: `${DB_SERVER}`, port: Number(DB_PORT), dialectOptions: { options: {} } });
        }

        let lastErr = null;
        for (const c of localCandidates) {
            const s = buildSequelize(c);
            try {
                // attempt to authenticate with short timeout
                console.log('[db_sqlaccount_client] trying candidate', c);
                await tryAuthenticateWith(s, 5000);
                console.log('[db_sqlaccount_client] authenticate succeeded with', c);
                real = s;
                return real;
            } catch (err) {
                console.warn('[db_sqlaccount_client] candidate failed:', err && err.message ? err.message : err);
                try { await s.close(); } catch (e) {}
                lastErr = err;
            }
        }

        // if all candidates failed, throw last error
        throw lastErr || new Error('Unable to authenticate (no candidates)');
    }

    // Minimal proxy API used by app: authenticate, query, transaction, close
    return {
        async authenticate() {
            await ensureReal();
        },
        async query(sqlText, options) {
            const s = await ensureReal();
            return s.query(sqlText, options);
        },
        async transaction(...args) {
            const s = await ensureReal();
            return s.transaction(...args);
        },
        async close() {
            if (real) {
                try { await real.close(); } catch (e) {}
            }
        }
    };
};