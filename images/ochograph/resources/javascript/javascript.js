var reloadIntervalInSeconds = 30;
var autoReload = false;

//function initAutoReload(enable) {
//    autoReload = enable;
//    if (autoReload) {
//        window.setTimeout(reloadPageIfEnabled, reloadIntervalInSeconds * 1000);
//    }    
//}

//function toggleAutoReload() {
//    autoReload = !autoReload;
//    var theSpan = document.getElementById('autoReloadId');
//    if (autoReload) {
//        theSpan.innerHTML = "on"; 
//        initAutoReload(true);
//    }
//    else {
//        theSpan.innerHTML = "off";
//    }
//}

//function reloadPageIfEnabled() {    
//    if (autoReload) {
//        location.href = location.origin + location.pathname + '?autoReload=1';
//    }
//}

function nodeClicked(node, rootPath) {
    $.ajax({
      url: rootPath + '/pod/info',
      data: {podId: node},
      cache: false
    })
    .done(function(html) {
        $("#theDialog").empty().append(html);
        // Once done retrieving the info, lets us get the logs.
        $.ajax({
          url: rootPath + '/pod/log',
          data: {podId: node},
          cache: false
        })
        .done(function(html) {       
            $("#theDialog").append(html).dialog({
              modal: true,
              buttons: {
                    Close: function(){
                        $(this).dialog("close");
                    }
                },
              maxHeight: 600,
              width: 800,
              open: function() {
                $('.ui-widget-overlay').addClass('custom-overlay');
              },
              close: function() {
                $('.ui-widget-overlay').removeClass('custom-overlay');
              },
              title: node
            });
        
        });
    });
}

var monthNames = [
  "January", "February", "March",
  "April", "May", "June", "July",
  "August", "September", "October",
  "November", "December"
];
function getNowFormated() {
    var date = new Date();
    var day = date.getDate();
    var monthIndex = date.getMonth();
    var year = date.getFullYear();
    var hours = date.getHours();
    var minutes = date.getMinutes();
    var seconds = date.getSeconds();
    
    return "" + hours + ":" + (minutes<10?"0":"") +  minutes + ":" + (seconds<10?"0":"") + seconds + ", " + day + " " + monthNames[monthIndex] + " " + year;
}

var contentData = undefined;
function loadContent(withImage, rootPath) {
    $.ajax({
        dataType: "json",
        url: rootPath + '/data',
        cache: false,
        success: function (data) {
                    dataString = JSON.stringify(data);
                    if (dataString != contentData) {
                        contentData = dataString;
                        var theUrl = undefined;
                        if (withImage) {
                            theUrl = rootPath + '/image/content';
                        }
                        else {
                            theUrl = rootPath + '/text/content';
                        }
                        $.ajax({
                          url: theUrl,
                          method: 'POST',
                          contentType: 'application/json',
                          cache: false,
                          data: dataString
                        })
                        .done(function(html) {
                            $("#theContent").empty().append(html);
                            $("#lastCheckDate").empty().append(getNowFormated());
                            $("#lastUpdateDate").empty().append(getNowFormated());
                        });
                    }
                    else {
                        $("#lastCheckDate").empty().append(getNowFormated());
                    }
                    
                },
        error: function (jqXHR, textStatus, errorThrown) {
                $("#theContent").empty().append("<span class=\"title\">Ochograph</span><br/><br/><span class=\"fail\">Error (re-)loading content, please try to reload the page manually.</span>")
            },
        complete: function() {
                window.setTimeout(function() {
                    loadContent(withImage, rootPath);
                }, reloadIntervalInSeconds * 1000);    
            }
    });
        
}