(async () => {
    const { sequelize } = require('../config/db_config');
    console.log('[test-db-auth] env DB_SERVER=', process.env.DB_SERVER, 'DB_INSTANCE=', process.env.DB_INSTANCE, 'DB_PORT=', process.env.DB_PORT);
    console.log('[test-db-auth] Starting authenticate test...');
    const timeoutMs = 20000;

    function withTimeout(p, ms) {
        return Promise.race([
            p,
            new Promise((_, rej) => setTimeout(() => rej(new Error('TIMEOUT')) , ms))
        ]);
    }

    try {
        const start = Date.now();
        await withTimeout(sequelize.authenticate(), timeoutMs);
        console.log('[test-db-auth] authenticate OK in', (Date.now() - start), 'ms');
    } catch (err) {
        console.error('[test-db-auth] authenticate failed:', err && err.message ? err.message : err);
    } finally {
        try { await sequelize.close(); } catch (e) {}
    }
})();
