var fs = require('fs');
var irc = require('irc');

var client = new irc.Client('irc.mozilla.org', 'devbot', {channels: ['#amo-bots']});

fs.readFile('./media/git-rev.txt', function(err, data) {
    client.say('#amo-bots', 'dev is updated: ' + data);
    client.disconnect();
});
