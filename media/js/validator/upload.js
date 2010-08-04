window.disable_submit = false;
function can_use_file_access() {return "FileReader" in window;}
function set_upload_enabled(enabled, type) {
	var disabled = !enabled;
	document.getElementById("submit_button").disabled = disabled;
	var d_ext = document.getElementById("disab_ext"),
	    d_addon = document.getElementById("disab_addon");
	d_addon.style.display = "none";
	d_ext.style.display = "none";
	switch(type) {
		case "ext":
			d_ext.style.display = "inline";
			break;
		case "addon":
			d_addon.style.display = "inline";
			break;
	}
	window.disable_submit = disabled;
}
function try_submit() {
	if(window.disable_submit) {
		alert("{{ _('You must choose a JAR or XPI file.') }}");
		return false;
	}
	return true;
}
function validate_package(filename, control) {
	
	if(filename == "" || filename.length < 5)
		return set_upload_enabled(false, "ext");
	
	var ext = filename.substr(-4);
	if(ext != ".xpi" && ext != ".jar") {
		return set_upload_enabled(false, "ext");
	}
	
	if(can_use_file_access()) {
		try {
			var blob = control.files[0];
			var reader = new FileReader();
			reader.onload = function(e) {
				var data = e.target.result.substr(0, 2);
				if(data == "PK") {
					set_upload_enabled(true);
				}
			};
			reader.readAsText(blob);
			return set_upload_enabled(false, "addon");
		} catch(e) {
			return set_upload_enabled(false, "addon");
		}
	}
	
	return set_upload_enabled(true);
	
}

function do_upload(form, allowed) {
	// Block incompatible files outright.
	if(!allowed)
		return false;
	
	var file_input = document.getElementById("file_input");
	
	// Fall back on standard HTTP transmission for older browsers.
	if(!can_use_file_access())
		return true;
	
	var file = file_input.files[0];
	var reader = new FileReader();
	reader.onload = function(e) {
		var uri = "save/?ajax=true";
		var boundary = "xxxxxxxxx";
		
		var xhr = new XMLHttpRequest();
		
		if("upload" in xhr) {
			xhr.upload.addEventListener(
				"progress",
				function(e){
					console.log((e.position / e.totalSize)*100);
				},
				false
			);
			
		}
		
		xhr.open("POST", uri, true);
		xhr.setRequestHeader("Content-Type", "multipart/form-data; boundary="+boundary);
		xhr.setRequestHeader("Content-Length", e.target.result.length);
		
		xhr.onreadystatechange = function() {
			if(xhr.readyState == 4) {
				if(xhr.status == 200 || xhr.status == 304) {
					if(xhr.responseText == '{"error":true}') {
						return;
					}
					window.location.href = xhr.responseText;
				}
			}
		};
		
		var filedata = e.target.result;
		var body = "--" + boundary + "\r\n";
		body += 'Content-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n';
		body += form.csrfmiddlewaretoken.value;
		body += "\r\n";
		body += "--" + boundary + "\r\n";
		body += 'Content-Disposition: form-data; name="addon"; filename="' + file.name + '"\r\n';
		body += "Content-Type: application/x-xpinstall\r\n\r\n";
		body += filedata + "\r\n";
		body += "--" + boundary + "--";
		
		xhr.send(body);
		return true;
		
	};
	reader.onerror = function(e) {
		if("console" in window)
			window.console.log(e);
	};
	reader.readAsDataURL(file);
	// Let the asynchronous groove take you away...
	return false;
}