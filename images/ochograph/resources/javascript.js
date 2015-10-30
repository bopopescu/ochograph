var reloadIntervalInSeconds = 10;
var autoReload = false;

function initAutoReload(enable) {
    autoReload = enable;
    if (autoReload) {
        window.setTimeout(reloadPageIfEnabled, reloadIntervalInSeconds * 1000);
    }    
}

function toggleAutoReload() {
    autoReload = !autoReload;
    var theSpan = document.getElementById('autoReloadId');
    if (autoReload) {
        theSpan.innerHTML = "on"; 
        initAutoReload(true);
    }
    else {
        theSpan.innerHTML = "off";
    }
}

function reloadPageIfEnabled() {    
    if (autoReload) {
        location.href = location.origin + location.pathname + '?autoReload=1';
    }
}