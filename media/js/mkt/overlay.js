(function() {
    z.body.on('touchmove', '.overlay', function(e){
        e.preventDefault();
        e.stopPropagation();
    });

    function dismiss() {
        var $overlay = $('.overlay.show');
        if ($overlay.length) {
            $overlay.removeClass('show');
            z.body.removeClass('overlayed');
            $(window).trigger('overlay_dismissed');
        }
    }

    z.page.on('fragmentloaded', function(e) {
        // Dismiss overlay when we load a new fragment.
        dismiss();
    });

    // Dismiss overlay when we click outside of it.
    z.win.on('click', '.overlay', function(e) {
        if ($(e.target).parent('body').length) {
            dismiss();
        }
    }).on('keydown.overlayDismiss', function(e) {
        if (!fieldFocused(e) && e.which == z.keys.ESCAPE) {
            e.preventDefault();
            dismiss();
        }
    }).on('dismiss', '.overlay', dismiss
    ).on('click', '.overlay .dismiss', _pd(dismiss));

})();

function notify(msg, title) {
    $('#msg-overlay').remove();

    var $overlay= $('<div id="msg-overlay" class="overlay">');
    var $section = $('<section>');
    if (title) {
        $section.append($('<h3>').text(title));
    }
    $section.append($('<p>').text(msg));
    $section.append($('<button class="dismiss">').text(gettext('OK')));
    $overlay.append($section);
    z.body.append($overlay);
    $overlay.addClass('show');
}

z.win.on('notify', function(e, o) {
    if (!o.msg) return;
    notify(o.msg, o.title);
});