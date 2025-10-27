const { exec } = require('child_process');
exec('sc query state= all', {timeout: 5000}, (err, stdout, stderr) => {
  if (err) {
    console.error('err', err.message || err);
    return;
  }
  const lines = stdout.split(/\r?\n/);
  const matches = lines.filter(l => /MSSQL/i.test(l));
  console.log('Found lines:', matches.slice(0,50).join('\n'));
});
