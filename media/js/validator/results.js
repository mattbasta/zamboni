$(document).ready(function() {
	
	$("#validator-view input").each(function(index) {
		
		$(this).click(function() {
			
			var is_checked = this.checked;
			$("." + this.value).each(function(index) {
				
				if(!("xdisabler" in this))
					this.xdisabler = 0;
				this.xdisabler += is_checked ? -1 : 1;
				
				if(this.xdisabler == 0)
					$(this).slideDown();
				else if(!is_checked)
					$(this).slideUp();
				
			});
			
			$(".-i-" + this.value).each(function(index) {
				
				if(!("xdisabler" in this))
					this.xdisabler = 0;
				this.xdisabler += is_checked ? -1 : 1;
				
				if(this.xdisabler == 0)
					$(this).removeAttr("disabled");
				else if(!is_checked)
					$(this).attr("disabled", "disabled");
				
			});
			
		});
		
	});
	
});