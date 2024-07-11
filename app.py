#!/usr/bin/env python
# PROJECT: PyXantech
# A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers
# by ProfessorC  (professorc@gmail.com)
# https://github.com/grandexperiments/pyxantech
# FILE: app.py - main routines
'''
    v.1.0 - Initial release  2019/03/25 - GNU General Public License (GPL 2.0)
    v.2.0 - Python 3/Pico release  2023/10/25 - GNU General Public License (GPL 2.0)
'''

'''
    For Pandora, I'm now using PianoBar/PatioBar. PianoBar is the headless Pandora player, and
    PatioBar is a separate project to build a web UI for PianoBar. I installed both on a separate
    Raspberry Pi, using the guide here: 
        https://thisdavej.com/creating-a-raspberry-pi-pandora-player-with-remote-web-control/
    which allowed me to put a HiRes DAC hat on that Pi and connect it directly to the Xantech
    amp. Then, in the congif.json file for PyXantech, simply add the source, give it a type
    of "streaming", and set the URL to the PatioBar UI - it will look something like this:
        http://192.168.xxx.xxx:3000/
    PyXantech will automatically add a tab at the top of it's interface to control PianoBar.
'''

## GLOBAL SETTINGS (adjust as necessary)
ACTIVE_SERIAL=True  # is actual Xantech connected to serial port (if 'False', use simulator)
ACTIVE_DEBUG=True  # is debugger active

# The USB port to use on the Raspberry Pi. This can usually be left as '/dev/ttyUSB0',
# but if you have multiple devices connected to your Pi, you may need to adjust this
# value. See Raspberry Pi documentation on specifying USB ports.
ACTIVE_USBPORT="/dev/ttyUSB0"

# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
SOCKET_ASYNC_MODE = None # 'eventlet'

# Xantech device setup
XANTECH_SOURCES = 8
XANTECH_ZONES = 8


# ****   HERE BE DRAGONS!!   ****
# **** NO OTHER EDITS NEEDED ****


# LIBRARIES (in alphabetical order)
import atexit
import eventlet
eventlet.monkey_patch()
# import flask
# import flask_socketio
from flask import Flask, render_template, session, request
from flask_socketio import SocketIO, emit, disconnect
import io
import json
import os
from os.path import dirname, abspath, join
if(ACTIVE_SERIAL):
    print("Using serial port: "+ACTIVE_USBPORT)
    import serial
    print("PySerial ver: " + serial.__version__)
else:
    # use Xantech simulator as fake serial
    print("Using serial port simulator")
    # use a spare serial port as a stub for the simulator
    ACTIVE_USBPORT="/dev/tty0"
    import xantech_sim as serial
from subprocess import call
import sys
import time


app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

socketio = SocketIO(app, async_mode=SOCKET_ASYNC_MODE, exclusive=True)

# keep track of connected clients (TODO: auto disconnect clients after inactivity?)
connected_clients={}

serial_port=serial.Serial(port=ACTIVE_USBPORT, baudrate=9600, bytesize=8, parity='N', stopbits=1, timeout=None, xonxoff=False, rtscts=False, write_timeout=None, dsrdtr=False )


# load and store zone and source names from config json
xantech_config = {
    "zones" : [],
    "sources" : []
}

config_filename = 'config.json'
homepath = dirname(abspath(__file__))
full_filename=join(homepath,config_filename)
f = open(full_filename)
xantech_config = json.load(f)
f.close()


# zone status ('?1ZD+')
# "#1ZS PR1 SS3 VO7 MU0 TR7 BS7 BA32 LS0 PS0"

# helpful dictionary to translate Xantech shortcodes to true actions
status_dict={
    'ZS':'status',
    'PR':'power',
    'SS':'source',
    'VO':'volume',
    'MU':'mute',
    'TR':'treble',
    'BS':'bass',
    'BA':'balance',
    'LS':'linked',
    'PS':'paged'
}

## HELPER FUNCTIONS
# left() - pythonized string left
def left(s, amount):
    return s[:amount]

## mid() - pythonized string mid
def mid(s, offset, amount):
    return s[offset:offset+amount]

## debug_log() - print to console (turn off for production)
def debug_log(msg):
    if(ACTIVE_DEBUG):
        print(msg)
    return True

# is_raspberry_pi() - check if app is running on Raspberry Pi
# credit: Arthur Barseghyan
# from: https://raspberrypi.stackexchange.com/questions/5100/detect-that-a-python-program-is-running-on-the-pi
# (used during the shutdown/reboot routes for the Pi... don't want to shut down dev server if it's not a Pi)
def is_raspberry_pi():
    debug_log("CALL is_raspberry_pi")
    found = False
    try:
        with io.open('/proc/cpuinfo', 'r') as cpuinfo:
            found = False
            for line in cpuinfo:
                trimmedLine = line.strip()
                debug_log("is_raspberry_pi/cpuinfo: "+trimmedLine)
                if trimmedLine.startswith('Hardware'):
                    found = True
                    label, value = trimmedLine.split(':', 1)
                    value = value.strip()
                    debug_log("is_raspberry_pi/Hardware: "+value)
                    if value not in (
                        'BCM2708',
                        'BCM2709',
                        'BCM2835',
                        'BCM2836'
                    ):
                        return False
            if not found:
                return False
    except:
        return False
    debug_log("Is Raspberry Pi: "+found)
    return found



## SERIAL FUNCTIONS
# send_command() - send a command/query out to the Xantech
def send_to_device(message):
    debug_log("CALL send_to_device")
    debug_log(message)
    response_string=''
    response_character=''
        
    try:
        serial_port.flushInput()
        serial_port.flushOutput()
        # serial_port.write(message)
        serial_port.write(message.encode('utf-8'))
        running=True;
        while running:
            response_character=str(serial_port.read(1).decode('utf-8'))
            # response_character=str(serial_port.read(1))
            if response_character != '!' and response_character != '?' and response_character != '+':
                response_string+=response_character
            if (response_character == '+') or (response_string.strip()=='ERROR') or (response_string.strip()=='OK'): 
                running=False
    except Exception as e:
        print(e)
    debug_log("Serial response: " + response_string)
    return response_string


# get_zone_status() - get the current status data for a zone
def get_zone_status(zone):
    debug_log("CALL get_zone_status")
    status={}
    command=str('?'+str(zone)+'ZD+')
    status_string=send_to_device(command)
    status_array=status_string.split(' ')
    for status_item in status_array:
        status_key=status_dict.get(left(status_item,2), '')
        if len(status_key)>0:
            status[status_key] = int(mid(status_item,2,len(status_item)))
    debug_log(status)
    return status

def get_all_zone_status():
    debug_log("CALL get_all_zone_status")
    for z in xantech_config["zones"]:
        if z["enabled"]:
            zone_status = get_zone_status(z["zone"])
            emit('set_status',
                {'zone':z["zone"],
                'command':'?'+str(z["zone"])+'ZS+',
                'status':zone_status},
                broadcast=True)


# goodbye() - register an exit event
@atexit.register
def goodbye():
    debug_log("CALL goodbye")
    print("Exiting...")


## SOCKET IO EVENTS
# pyxantech_message() - event testing loopback, except a message and send it back to the UI
@socketio.on('xantech_message', namespace='/pyxantech')
def pyxantech_message(message):
    debug_log("CALL pyxantech_message")
    debug_log(message)
    emit('xantech_response',
         {'data': message['data'] }
        )

# pyxantech_zone_all_status() - get and return the status data for ALL zones
# had to move this to serverside because socket async was causing collisions
@socketio.on('xantech_all_status', namespace='/pyxantech')
def pyxantech_zone_all_status(msg):
    debug_log('CALL pyxantech_zone_all_status')
    get_all_zone_status()


# pyxantech_zone_status() - get and return the status data for a zone
@socketio.on('xantech_status', namespace='/pyxantech')
def pyxantech_zone_status(message):
    debug_log('CALL pyxantech_zone_status')
    zone=message['zone']
    zone_status = get_zone_status(zone)
    # zone_status = ""
    emit('set_status',
         {'zone':zone,
          'command':'?'+str(zone)+'ZS+',
          'status':zone_status},
          broadcast=True)

# pyxantech_command() - send a command/query to Xantech
@socketio.on('xantech_command', namespace='/pyxantech')
def pyxantech_command(message):
    debug_log("CALL pyxantech_command")
    debug_log(message)
    zone=message['zone']
    command=message['command']
    volume=message['volume']
    status=send_to_device(command)
    if zone == 0:
        get_all_zone_status()
    else:
        if status == "OK":
            status=get_zone_status(zone)

        if status["volume"]==0:
            status["volume"]=volume

        emit('set_status',
            {'zone':zone,
            'command':command,
            'status':status},
            broadcast=True)




# pyxantech_connect() - client has connected to socket
@socketio.on('connect', namespace='/pyxantech')
def pyxantech_connect():
    debug_log("CALL pyxantech_connect")

    clientId=""
    if(hasattr(request,'sid')):
        clientId=request.sid

    debug_log('Client connected: '+clientId)
    emit('xantech_response',
         {'data': 'Connected',
          'count': 0})
    # for zone in range(1, 8):
    #     print('Initial status of: '+str(zone))
    #     emit('set_status', {'zone':zone,'command':'?'+str(zone)+'ZS+','status':get_zone_status(zone)}, broadcast=True)
    emit('done_loading',
         {'data': 'Send Done Loading'})
    if clientId not in connected_clients:
        connected_clients[clientId] = time.time()
    print('* Clients remaining: '+str(len(connected_clients)))

# pyxantech_disconnect() - client has disconnected from socket
@socketio.on('disconnect', namespace='/pyxantech')
def pyxantech_disconnect():
    debug_log("CALL pyxantech_disconnect")
    clientId=""
    if(hasattr(request,'sid')):
        clientId=request.sid


    print('Client disconnected', clientId)
    if clientId in connected_clients:
        del connected_clients[clientId]
    print('* Clients remaining: '+str(len(connected_clients)))

# pyxantech_disconnect_request() - client has requested to disconnect
# had to create this stub to solve some issues calling disconnect directly (TODO: research direct disconnect issues)
@socketio.on('disconnect_request', namespace='/pyxantech')
def pyxantech_disconnect_request(message):
    debug_log("CALL pyxantech_disconnect_request")
    emit('xantech_response',
         {'data': 'Disconnected!'})
    disconnect()


## FLASK ROUTES
# app_exit() - route to shutdown app
@app.route('/exit')
def app_exit():
    debug_log("CALL app_exit")
    try:
        sys.exit(0)
    except Exception as e:
        print("Exit exception:"+ str(e))
    else:
        debug_log("No exception in exiting")

# app_restart() - route to restart app (experimental)
# (TODO: throws exception about reusing the socket - figure out how to close socket before restart)
@app.route('/restart')
def app_restart():
    debug_log("CALL app_restart")
    try:
        # try to close all connections first
        # disconnect()
        os.execl(sys.executable, os.path.abspath(__file__), *sys.argv)
    except Exception as e:
        print("Restart exception:"+ str(e))
    else:
        # could not restart, just exit instead
        debug_log("No exception in restarting")

# pi_shutdown() - route to shutdown the whole pi (experimental)
@app.route('/shutdown')
def pi_shutdown():
    debug_log("CALL pi_shutdown")
    if(is_raspberry_pi()):
        # try to close all connections first
        try:
            call("sudo nohup shutdown -P now", shell=True)
        except Exception as e:
            # could not shutdown, just exit app instead
            debug_log("There was a problem shutting down the system. "+ str(e) +"Exiting app instead.")
            app_exit()
    else:
        debug_log("Could not shutdown non-Raspberry Pi system. Exiting app instead.")
        app_exit()
    return True

# pi_reboot() - route to reboot the whole pi (experimental)
@app.route('/reboot')
def pi_reboot():
    debug_log("CALL pi_reboot")
    try:
        if(is_raspberry_pi()):
            # try to close all connections first
            # disconnect()
            call("sudo nohup shutdown -r now", shell=True)
        else:
            debug_log("Could not restart non-Raspberry Pi system. Restarting app instead.")
            app_restart()
    except:
        # could not restart, just restart app instead
        debug_log("There was a problem restarting down the system. Restarting app instead.")
        app_restart()


# index() - main route to display default UI page
@app.route('/')
def index():
    debug_log("CALL index")
    stageMarkup=''

    streaming = []
    

    # determine if any of the sources are streaming media
    print("Loading sources...")
    for source in xantech_config["sources"]:
        debug_log(source)
        if source["type"]=="streaming":
            streaming.append(source)
    debug_log(streaming)

    # print("Loading zones...")
    # for zone in xantech_config["zones"]:
    #     print(zone)
    #     if source["type"]=="streaming":
    #         streaming.append(source)
    # print(streaming)


    print("Loading UI...")
    return render_template('dashboard-template.html',
                           sources=xantech_config["sources"],
                           zones=xantech_config["zones"],
                           streaming=streaming,
                           async_mode=socketio.async_mode)

# Flask app init
# use socketio.run(), instead of app.run(), to solve socketio issues and run app in production-ready environment
if __name__ == '__main__':
    socketio.run(app, debug=ACTIVE_DEBUG,host='0.0.0.0')