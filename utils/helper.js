const fs = require('fs');
const path = require('path');
const moment = require('moment-timezone');

class Helper {


    randomString(length) {
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
        let result = '';
        for (let i = 0; i < length; i++) {
            result += chars.charAt(Math.floor(Math.random() * chars.length));
        }
        return result;
    }   
    
    setConsoleTitle(title) {
        if (process.platform === 'win32') {
            process.stdout.write(`\x1b]0;${title}\x07`);
        }
    }


    loadHistoricalConfig() {
        try {
            const configPath = this.getWritableConfigPath();
            if (fs.existsSync(configPath)) {
                const content = fs.readFileSync(configPath, 'utf-8');
                return JSON.parse(content);
            }
            return { last_time: '' };
        } catch (error) {
            console.error('Error loading historical config:', error);
            return { last_time: '' };
        }
    }


    saveHistoricalConfigLastTime(lastTime) {
        try {
            const configPath = this.getWritableConfigPath();
            const config = this.loadHistoricalConfig();
            config.last_time = lastTime;

            // Create directory if it doesn't exist
            const dir = path.dirname(configPath);
            if (!fs.existsSync(dir)) {
                fs.mkdirSync(dir, { recursive: true });
            }

            fs.writeFileSync(configPath, JSON.stringify(config, null, 4), 'utf-8');
            console.log(`✅ Saved last_time: ${lastTime} to ${configPath}`);
            return true;
        } catch (error) {
            console.error('Error saving historical config:', error);
            return false;
        }
    }


    getWritableConfigPath() {
        if (process.pkg) {
            // Running as executable
            return path.join(path.dirname(process.execPath), 'historical_config.json');
        } else {
            // Running as script
            return path.join(__dirname, '..', 'historical_config.json');
        }
    }


    calculateBarsNeeded(lastTime) {
        const defaultBars = 30000;

        if (!lastTime) {
            return defaultBars;
        }

        try {
            const lastDateTime = moment.utc(lastTime, 'YYYY-MM-DD HH:mm:ss');
            const now = moment.utc();
            const diffMinutes = now.diff(lastDateTime, 'minutes');
            const requiredBars = Math.max(1, diffMinutes);

            return requiredBars <= 300 ? requiredBars + 300 : requiredBars;
        } catch (error) {
            console.error('Error calculating bars:', error);
            return defaultBars;
        }
    }

}

module.exports = Helper;