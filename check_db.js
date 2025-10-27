const { sequelize } = require('./config/db_config');
const { QueryTypes } = require('sequelize');

async function checkTables() {
    try {
        await sequelize.authenticate();
        console.log('✅ Connected');

        // Check available tables
        const tables = await sequelize.query("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'", { type: QueryTypes.SELECT });
        console.log('All tables:');
        tables.slice(0, 20).forEach(table => {
            console.log('  ', table.TABLE_SCHEMA + '.' + table.TABLE_NAME);
        });

        // Check stored procedure definition
        try {
            const procDef = await sequelize.query("SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.InsertBulkTimeframeData')) AS Definition", { type: QueryTypes.SELECT });
            console.log('\nStored procedure definition:');
            console.log(procDef[0].Definition);
        } catch (error) {
            console.log('Could not get procedure definition:', error.message);
        }

    } catch (error) {
        console.error('Error:', error.message);
    } finally {
        await sequelize.close();
    }
}

checkTables();