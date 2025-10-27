const dotenv = require('dotenv');
const res = dotenv.config();
console.log('dotenv result:', res.error ? res.error : 'OK');
console.log('parsed keys:', Object.keys(res.parsed || {}).slice(0,50));
console.log('DB_SERVER=', process.env.DB_SERVER);
console.log('DB_INSTANCE=', process.env.DB_INSTANCE);
console.log('raw .env file snippet:');
const fs = require('fs');
console.log(fs.readFileSync('.env','utf8'));
