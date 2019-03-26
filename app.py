#!/usr/bin/env python2.7
# PROJECT: PyXantech
# A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers
# by ProfessorC  (professorc@gmail.com)
# https://github.com/grandexperiments/pyxantech
# FILE: app.py - main routines
'''
    v.1.0 - Inital release  2019/03/25 - GNU General Public License (GPL 2.0)
'''

'''
    For Pandora, I've been using MusicBox/MPD but I've removed this functionality
    due to issues. They may be added in a future release, or feel free to add
    functionality yourself.
'''


## GLOBAL SETTINGS (adjust as necessary)
ACTIVE_SERIAL=True  # is actual Xantech connected to serial port (if 'False', use simulator)
ACTIVE_MPD=False    # is MusicBox active (deleted, to return later?)
ACTIVE_DEBUG=False  # is debugger active

# The USB port to use on the Raspberry Pi. This can usually be left as '/dev/ttyUSB0',
# but if you have multiple devices connected to your Pi, you may need to adjust this
# value. See Raspberry Pi documentation on specifying USB ports.
ACTIVE_USBPORT="/dev/ttyUSB0"   






# **** NO OTHER EDITS NEEDED ****


# LIBRARIES (in alphabetical order)
import atexit
import eventlet
from flask import Flask, render_template, session, request
from flask_socketio import SocketIO, emit, disconnect
import io
import json
import os
from os.path import dirname, abspath, join
if(ACTIVE_SERIAL):
    import serial
else:
    # use Xantech simulator as fake serial
    import xantech_sim as serial
from subprocess import call
import sys
import time


    

# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode=async_mode)


# keep track of connected clients (TODO: auto disconnect clients after inactivity?)
connected_clients={}

# (TODO: investigate only opening serial on commands?)
usb_port = ''
serial_port=serial.Serial(ACTIVE_USBPORT, timeout=1, baudrate=9600)

# store zone and source names
# (TODO: change zones.txt and sources.txt to JSON for more configuration options)
zone_array=[]
source_array=[]

# playlist would come from MusicBox/MPD dynamically. Filled here only as example
# (TODO: restore MusicBox functionality for Pandora/internet radio)
playlist=[{'num':1,'name':'Brunch Cafe'},{'num':2,'name':'Classic Rock'},{'num':3,'name':'Dad Rock'},{'num':4,'name':'60s Oldies'}]

# helpful dictionary to translate Xantech shortcodes to true actions
status_dict={
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
        print msg
    return True 

# is_raspberry_pi() - check if app is running on Raspberry Pi
# credit: Arthur Barseghyan
# from: https://raspberrypi.stackexchange.com/questions/5100/detect-that-a-python-program-is-running-on-the-pi
# (used during the shutdown/reboot routes for the Pi... don't want to shut down dev server if it's not a Pi)
def is_raspberry_pi():
    try:
        with io.open('/proc/cpuinfo', 'r') as cpuinfo:
            found = False
            for line in cpuinfo:
                print(line)
                if line.startswith('Hardware'):
                    found = True
                    label, value = line.strip().split(':', 1)
                    value = value.strip()
                    if value not in (
                        'BCM2708',
                        'BCM2709',
                        'BCM2835',
                        'BCM2836'
                    ):
                        return False
            if not found:
                return False
    except IOError:
        return False

    return False

## CONFIG FUNCTIONS
# read_conf_file() - read a generic config file into an array
def read_conf_file(filename):
    homepath = dirname(abspath(__file__))
    debug_log(homepath)
    full_filename=join(homepath,filename)
    lines = open(full_filename).read().splitlines()
    line_array=[];
    for line_num,line_name in enumerate(lines,start=1):
        debug_log(str(line_num)+': '+str(line_name)+'\r')
        line_array.append(
            {
                'num':int(line_num),
                'name':str(line_name)
            }
        )
        debug_log(line_array)
    return line_array

# load_sources() - load the config file for sources
def load_sources():
    return read_conf_file('sources.txt')

# load_znoes() - load the config file for zones
def load_zones():
    zones=read_conf_file('zones.txt')
    print '**LOADING ZONES\r'
    debug_log(zones)
    return zones



## SERIAL FUNCTIONS
# send_command() - send a command/query out to the Xantech
def send_command(command):
    response_string=''
    response_character=''
    serial_port.flushInput()
    serial_port.flushOutput()
    serial_port.write(command.encode());
    running=True;
    while running:
        response_character=str(serial_port.read(1))
        if response_character != '!' and response_character != '?' and response_character != '+':
            response_string+=response_character
        if (response_character == '+') or (response_string.strip()=='ERROR') or (response_string.strip()=='OK'): running=False
    return response_string

# get_zone_status() - get the current status data for a zone
def get_zone_status(zone,volume):
    status={}
    command=str('?'+str(zone)+'ZD+')
    if not volume.isdigit():
        volume=0;

    status_string=send_command(command)
    status_array=status_string.split(' ')
    for status_item in status_array:
        status_key=status_dict.get(left(status_item,2), '')
        if len(status_key)>0:
            status[status_key] = int(mid(status_item,2,len(status_item)))
            if(status_key == 'volume'):
                '''
                    because the MRC88 is nice
                    and reduces the volume when
                    turning on and switching sources
                    then brings it back up slowly,
                    the first query for the volume
                    will probably not be accurate...
                    so we take the existing setting
                    from the interface itself instead
                    of the value returned from ZD
                '''
                if volume > 0:
                    print('Changing volume val')
                    status[status_key]=int(volume)   
    # status=json.dumps(outp)
    return status


# goodbye() - register an exit event
@atexit.register
def goodbye():
    print "Exiting..."
    
    
  
    

## SOCKET IO EVENTS
# pyxantech_message() - event testing loopback, except a message and send it back to the UI
@socketio.on('xantech_message', namespace='/pyxantech')
def pyxantech_message(message):
    debug_log(message)
    emit('xantech_response',
         {'data': message['data'] }
        )

# pyxantech_zone_status() - get and return the status data for a zone    
@socketio.on('xantech_status', namespace='/pyxantech')
def pyxantech_zone_status(message):
    debug_log('xantech_status')
    zone=message['zone']
    volume=message['volume']
    emit('set_status',
         {'zone':zone,
          'command':'?'+str(zone)+'ZS+',
          'status':get_zone_status(zone,volume)},
          broadcast=True)


# pyxantech_command() - send a command/query to Xantech
@socketio.on('xantech_command', namespace='/pyxantech')
def pyxantech_command(message):
    # session['receive_count'] = session.get('receive_count', 0) + 1
    debug_log(message);
    zone=message['zone']
    command=message['command']
    volume=message['volume']
    status=send_command(command)
    # emit('done_loading',{'data': 'Send Done Loading'})
    emit('set_status',
         {'zone':zone,
          'command':command,
          'status':get_zone_status(zone,volume)},
         broadcast=True)

# pyxantech_connect() - client has connected to socket
@socketio.on('connect', namespace='/pyxantech')
def pyxantech_connect():

    clientId=""
    if(hasattr(request,'sid')):
        clientId=request.sid
    
    print('Client connected',clientId)
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
    emit('xantech_response',
         {'data': 'Disconnected!'})
    disconnect()


## FLASK ROUTES
# app_exit() - route to shutdown app
@app.route('/exit')
def app_exit():
    try:
        sys.exit(0)
    except Exception,e:
        print("Exit exception:"+ str(e))
    else:
        debug_log("No exception in exiting")
        
# app_restart() - route to restart app (experimental)
# (TODO: throws exception about reusing the socket - figure out how to close socket before restart)
@app.route('/restart')
def app_restart():
    try:
        # try to close all connections first
        # disconnect()
        os.execl(sys.executable, os.path.abspath(__file__), *sys.argv) 
    except Exception,e:
        print("Restart exception:"+ str(e))
    else:
        # could not restart, just exit instead
        debug_log("No exception in restarting")
    
# pi_shutdown() - route to shutdown the whole pi (experimental)
@app.route('/shutdown')
def pi_shutdown():
    if(is_raspberry_pi()):
        # try to close all connections first
        try:
            call("sudo nohup shutdown -P now", shell=True)
        except Exception,e:
            # could not shutdown, just exit app instead
            debug_log("There was a problem shutting down the system. "+ str(e) +"Exiting app instead.")
            app_exit()
    else:
        debug_log("Could not shutdown non-Raspberry Pi system. Exiting app instead.")
        app_exit()
    return true
        
# pi_reboot() - route to reboot the whole pi (experimental)
@app.route('/reboot')
def pi_reboot():
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
        
        
# stations() - display the stations.html template for Pandora/Radio
@app.route('/stations')
def stations():
    debug_log(playlist)
    return render_template('stations.html',
                           playlist=playlist,
                           async_mode=socketio.async_mode)
    
# index() - main route to display default UI page    
@app.route('/')
def index():
    stageMarkup=''
    
    print("Loading Sources...")
    sources=load_sources()
    print("Loading Zones...")
    zones=load_zones()
    zone_array=zones
    
    print("Loading UI...")
    return render_template('dashboard-template.html',
                           sources=sources,
                           zones=zones,
                           async_mode=socketio.async_mode)

# Flask app init
# use socketio.run(), instead of app.run(), to solve socketio issues and run app in production-ready environment
if __name__ == '__main__':
    socketio.run(app, debug=ACTIVE_DEBUG,host='0.0.0.0')
