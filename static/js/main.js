/*
    PROJECT: PyXantech
    A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers
    by ProfessorC  (professorc@gmail.com)
    https://github.com/grandexperiments/pyxantech
    FILE: main.js - foundation javascript


    v.1.0 - Inital release  2019/03/25 - GNU General Public License (GPL 2.0)
    v.2.0 - Python 3/JQuery Mobile 1.5 release  2023/10/25 - GNU General Public License (GPL 2.0)
*/

$(document).ready(function () {
    console.log("DOCUMENT READY");
    processZone(1, true);

    $(".zone").filter(function(index, element){
        return index % 2 == 1;
    }).addClass("row-odd");            

    console.log("SOCKET CREATE:");
    console.log(location.protocol + '//' + document.domain + ':' + location.port + namespace);
    var socket = io.connect(location.protocol + '//' + document.domain + ':' + location.port + namespace);
    console.log(socket);

    function processZone(zone, mode) {
            console.log('PROCESSING ZONE');
            processingZones[zone] = mode;
            console.log(processingZones);
            if (processingZones.some(function(zone) {
                    return zone;
                })) {
                console.log("found some processing");
                loadingModal(true);
            } else {
                loadingModal(false);
            }
    }


    function loadingModal(mode, message) {
        //  if (mode && $(".loading-curtain").is(":hidden")) {
        if (mode) {
           console.log("opening modal");
            $(".loading-wrapper span").html("Loading...");
            $(".loading-curtain").fadeIn("slow");
            $(".loading-wrapper").fadeIn("slow");
        } else {
            console.log("closing modal");
            $(".loading-curtain").fadeOut("slow");
            $(".loading-wrapper").fadeOut("slow");
        }
    }
    
    function setStatus(zone, statusArray) {
        // processZone(zone,true);

        console.log(statusArray);
        console.log("setStatus: zone=" + zone);

        $('#powerButton' + zone).removeClass('PR0').removeClass('PR1').addClass('PR' + statusArray["power"]);

        // set VOLUME SLIDER value
        console.log("setting volume of zone " + zone + " to "+ statusArray["volume"]);
        var volumeSlider = $('#volumeSlider' + zone);
        volumeSlider.data("sendSocketEvent", false);
        volumeSlider.val(statusArray["volume"]);
        volumeSlider.trigger('change');
        volumeSlider.data("sendSocketEvent", true);

        // set SOURCE SELECTOR value
        console.log("setting source of zone " + zone + " to "+ statusArray["source"]);
        var sourceSelect = $('#sourceSelect' + zone);
        console.log(sourceSelect);
        sourceSelect.data("sendSocketEvent", false);
        sourceSelect.selectedIndex = statusArray["source"];
        sourceSelect.val(statusArray["source"]).attr('selected', true).siblings('option').removeAttr('selected');
        sourceSelect.data("sendSocketEvent", true);

        // statusArray["source"]
        /*
        $('#sourceSelect'+zone).selectedIndex = statusArray["source"];

        $('.zone' + zone + ' .source-select').val(statusArray["source"]).attr('selected', true).siblings('option').removeAttr('selected');
        */

        // $('.zone' + zone + ' select.source-select').selectmenu("refresh");

        processZone(zone, false);

        /*
        Power – On
        Source – 4
        Volume – 8
        Mute – Off
        Treble – 7
        Bass – 7
        Balance – 32
        Linked – No
        Paged – No      

        PR1 SS1 VO0 MU0 TR7 BS7 BA32 LS0 PS0
        */

    };


    function getStatus(zone) {
        console.log("getStatus called:" + zone);
        volume = $('.volume-slider-'+zone).val();
        socket.emit('xantech_status', {
            'zone': zone,
            'volume': volume
        });
        return true;
    };

    function getAllStatus() {
        // loadingModal(false);
        // TODO: pass the number of active zones in the UI to confirm on server
        socket.emit('xantech_all_status', { 'zones': 0 });
    };

    socket.on('connect', function() {
        socket.emit('xantech_message', {
            data: 'Client connected!'
        });
    });

    socket.on('done_loading', function(msg) {
        console.log("DONE LOADING");
        console.log(msg);
        getAllStatus();
    });

    socket.on('xantech_response', function(msg) {
        console.log(msg);
    });

    socket.on('set_status', function(msg) {
        console.log('ClientSide/set_status:');
        console.log(msg);
        console.log(msg.zone);
        setStatus(msg.zone, msg.status);
    });

    $(".power-button").click(function(event) {
        event.preventDefault();
        console.log("click power button")
        zone = $(this).data("zone");
        volume = $('#volumeSlider'+zone).val();
        
        console.log({
            'zone': zone,
            'volume':volume,
            'command': '!' + zone + 'PT+'
        });


        socket.emit('xantech_command', {
            'zone': zone,
            'volume':volume,
            'command': '!' + zone + 'PT+'
        });

        /*
        socket.emit('xantech_command', {
            'zone': $this.data('zone'),
            'command': $this.data('command')
        });
        */
        return false;
    });


    $(".source-select").on("change", function(event, ui) {
        var zone = $(event.currentTarget).data('zone');
        var source = $(event.currentTarget).val();
        var volume = $('#volumeSlider'+zone).val();

        if ($(this).data("sendSocketEvent")){
            socket.emit('xantech_command', {
                'zone': zone,
                'volume':volume,
                'command': '!' + zone + 'SS' + source + '+'
            });
            console.log('set zone ' + zone + ' to source: ' + source);
        }
    });

    $(".volume-slider").on("input",function(){
        // console.log("hi");
        var zone = $(this).data("zone");
        // console.log(zone);
        $("#volumeDisplay"+zone).val($(this).val());
    });
    $(".volume-slider").on("change",function(){
        console.log("volume changed");
        var zone = $(this).data("zone");
        var volume = $(this).val();
        $("#volumeDisplay"+zone).val(volume);

        if ($(this).data("sendSocketEvent")){
            console.log("send socket event");
            console.log({
            'zone': zone,
            'volume': volume,
            'command': '!' + zone + 'VO' + volume + '+'
            });
            socket.emit('xantech_command', {
                    'zone': zone,
                    'volume': volume,
                    'command': '!' + zone + 'VO' + volume + '+'
                });

            console.log('set zone ' + zone + ' to volume: ' + volume);
        }
    });

    $(".master-power-button").on("click", function(event, ui) {
            event.preventDefault();
            volume = $('#volumeSlider1').val();
            socket.emit('xantech_command', {
                'zone': 0,
                'volume': 0,
                'command':'!AO+'
            });
            // getAllStatus();
            /*
            socket.emit('xantech_command', {
                'zone': $this.data('zone'),
                'command': $this.data('command')
            });
            */
            return false;
    });

    $(window).on("beforeunload", function(){
        socket.emit('disconnect_request', {
            data: 'Client disconnecting!'
        });
    });


});

