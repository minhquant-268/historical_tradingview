const { Sequelize } = require('sequelize');
const dotenv = require('dotenv');
const tedious = require('tedious');
dotenv.config();


const DB_SERVER = process.env.DB_SERVER;
const DB_INSTANCE = process.env.DB_INSTANCE;
const DB_NAME = process.env.DB_NAME;
const DB_USER = process.env.DB_USER;
const DB_PASSWORD = process.env.DB_PASSWORD;
const DB_DRIVER = process.env.DB_DRIVER;
const DB_PORT = process.env.DB_PORT || 50123;


const DB_AUTH = (process.env.DB_AUTH || 'sql').toLowerCase();

let sequelize;
if (DB_AUTH === 'windows' || DB_AUTH === 'trusted') {
    // Sequelize configuration for MSSQL with Windows Authentication
    try {
        const winAuthClientFactory = require('./db_windows_client');
        // create client with DB params so client doesn't read process.env itself        
        sequelize = winAuthClientFactory({ server: DB_SERVER, instance: DB_INSTANCE, database: DB_NAME });
    } catch (err) {
        throw new Error("Error: " + err.message);
    }
} else {
    // SQL-account path: use a factory that creates a Sequelize instance with tedious
    try {
        const sqlAccountFactory = require('./db_sqlaccount_client');
        sequelize = sqlAccountFactory({ server: DB_SERVER, instance: DB_INSTANCE, database: DB_NAME, user: DB_USER, password: DB_PASSWORD, port: DB_PORT });
    } catch (err) {
        // fallback to the old inline Sequelize construction if factory fails
        throw new Error("Error: " + err.message);
    }
}

module.exports = {
    sequelize
};

