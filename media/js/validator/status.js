var validator = {
	field: "message",
	updateStatus : function() {
		
		if(!("$" in window))
			return;
		
		if(validator.mutex) {
			validator.failcount++;
			if(validator.failcount > 3) {
				return;
			}
		}
		
		validator.mutex = true;
		
		$.ajax({
			url : 'poll',
			dataType : 'json',
			data : '',
			timeout: 7500,
			success : function(data) {
				if(!data)
					return;
				if(!("status" in data)) {
					alert("There was an error processing your add-on.");
					window.location.href = "/validator/";
					return;
				}
				
				if(data.status == "done") {
					window.location.href = "/validator/result/" + window.task;
					return;
				}
				
				var status = document.getElementById("status");
				status.innerHTML = data[validator.field];
				
				validator.failcount = 0;
				validator.mutex = false;
				
			},
			error : function(x, status, error) {
				if("console" in window)
					console.log("AJAX error: " + status + ":" + error);
				validator.failcount--;
			}
		});
	},
	interval: null,
	mutex: false,
	failcount: 0,
	message_pos: 0
};
$(document).ready(function() {
	setTimeout(function() {
		validator.interval = setInterval(validator.updateStatus, 3000);
	}, 1000);
	setInterval(function() {
		var messages = [
			"Reticulating splines...",
			"Gravity check...",
			"Dividing by the square root of <i>e</i>...",
			"Finding the 1,000,000<sup>th</sup> digit of &pi;...",
			"Analyzing the DaVinci Code...",
			"Deciphering the Rosetta Stone...",
			"Singing the alphabet backwards..."
		];
		document.getElementById("waitplease").innerHTML = messages[validator.message_pos++ % messages.length];
	}, 3000);
});