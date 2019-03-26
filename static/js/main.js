/*
    PROJECT: PyXantech
    A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers
    by ProfessorC
    https://github.com/grandexperiments/pyxantech
    FILE: main.js - foundation javascript


    v.1.0 - Inital release  2019/03/25 - GNU General Public License (GPL 2.0)
*/


        $(document).ready(function() {
            console.log("DOCUMENT READY");
			$(".ui-slider-track").css("pointer-events","none"); 
			$(".ui-slider-track").css("disabled","disabled");

			$(".ui-slider-handle").css("pointer-events","all"); 


            $(".channel").each(function(index, element){
                console.log("what does this do?");
                return index % 3 == 2;
            }).addClass("row-even");            
            
            var socket = io.connect(location.protocol + '//' + document.domain + ':' + location.port + namespace);

            function processChannel(chan, mode) {
                console.log('PROCESSING CHANNEL');
                processingChannels[chan] = mode;
                console.log(processingChannels);
                if (processingChannels.some(function(channel) {
                        return channel;
                    })) {
                    loadingModal(true);
                } else {
                    loadingModal(false);
                }
            }

            
            function loadingModal(mode, message) {
                if (mode && $(".loading-curtain").is(":hidden")) {
                    $(".loading-wrapper span").html("Sending...");
                    $(".loading-curtain").fadeIn("slow");
                    $(".loading-wrapper").fadeIn("slow");
                } else {
                    $(".loading-curtain").fadeOut("slow");
                    $(".loading-wrapper").fadeOut("slow");
                }
            }

            
            function setStatus(chan, statusArray) {
                // processChannel(chan,true);

                console.log(statusArray);
                console.log("setStatus: channel=" + chan);

                $('.chan' + chan + ' .power-btn').removeClass('PR0').removeClass('PR1').addClass('PR' + statusArray["power"]);
                $('.chan' + chan + ' .volume-slider').val(statusArray["volume"]).slider("refresh");

                $('.chan' + chan + ' .source-select').selectedIndex = statusArray["source"];

                $('.chan' + chan + ' .source-select').val(statusArray["source"]).attr('selected', true).siblings('option').removeAttr('selected');

                $('.chan' + chan + ' select.source-select').selectmenu("refresh");

                processChannel(chan, false);

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


            function getStatus(channel) {
                console.log("getStatus called:" + channel);
                volume = $('.volume-slider-'+channel).val();
                socket.emit('xantech_status', {
                    'channel': channel,
                    'volume': volume
                });

                return true;
            };

            function getAllStatus() {
                // loadingModal(true);
                for (var channel = 0; channel < activeChannels.length; channel++) {
                    if (activeChannels[channel]) getStatus(channel);

                }
            };

            socket.on('connect', function() {
                socket.emit('my_event', {
                    data: 'I\'m connected!'
                });
            });

            socket.on('done_loading', function(msg) {
                console.log(msg);
                getAllStatus();
            });

            socket.on('my_response', function(msg) {
                console.log(msg);
            });

            socket.on('set_status', function(msg) {
                console.log(msg);
                console.log(msg.channel);
                setStatus(msg.channel, msg.status);
            });

            $(".power-btn").click(function(event) {
                event.preventDefault();
                channel = $(event.currentTarget).data('channel');
                volume = $('.volume-slider-'+channel).val();
                
                socket.emit('xantech_command', {
                    'channel': channel,
                    'volume':volume,
                    'command': '!' + channel + 'PT+'
                });

                /*
                socket.emit('xantech_command', {
                    'channel': $this.data('channel'),
                    'command': $this.data('command')
                });
                */
                return false;
            });

            $(".source-select").on("change", function(event, ui) {
                channel = $(event.currentTarget).data('channel');
                source = $(event.currentTarget).val();
                volume = $('.volume-slider-'+channel).val();
                console.log(volume);
                socket.emit('xantech_command', {
                    'channel': channel,
                    'volume':volume,
                    'command': '!' + channel + 'SS' + source + '+'
                });

                console.log('set channel ' + channel + ' to source: ' + source);
            });

            $(".volume-slider").on("slidestop", function(event, ui) {
                channel = $(event.currentTarget).data('channel');
                volume = $(event.currentTarget).val();

                socket.emit('xantech_command', {
                    'channel': channel,
                    'volume': volume,
                    'command': '!' + channel + 'VO' + volume + '+'
                });

                console.log('set channel ' + channel + ' to volume: ' + volume);
            });

            $(".master-power-button").on("click", function(event, ui) {
                event.preventDefault();
                volume = $('.volume-slider-1').val();
                socket.emit('xantech_command', {
                    'channel': 1,
                    'volume': volume,
                    'command':'!AO+'
                });
                getAllStatus();
                /*
                socket.emit('xantech_command', {
                    'channel': $this.data('channel'),
                    'command': $this.data('command')
                });
                */
                return false;
            });


        });

        /*
        $(document).on('stop', 'input[type=range]', function() {
            console.log("change");
        });  
        */

        /*
        $( ".volume-slider" ).slider({
  stop: function( event, ui ) {  console.log("change"); }
});
        */
        /*
        $('.volume-slider').on('mouseup',function(){
            $("#statusField").val("change");
            
        })
        */

        ///   });
