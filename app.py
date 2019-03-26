#!/usr/bin/env python2.7
# PROJECT: PyXantech
# A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers
# by ProfessorC
# https://github.com/grandexperiments/pyxantech
# FILE: app.py - main routines
'''
    v.1.0 - Inital release  2019/03/25 - GNU General Public License (GPL 2.0)
'''

'''
    For Pandora, I've been using MusicBox/MPD but I've removed those functions
    due to issues. They may be added in a future release, or feel free to add
    functionality yourself.
'''

import json
import atexit
import time

from os.path import dirname, abspath, join
from flask import Flask, render_template, session, request
from flask_socketio import SocketIO, emit, disconnect
import eventlet


ACTIVE_SERIAL=False
ACTIVE_MPD=False

if(ACTIVE_SERIAL):
    import serial
else:
    import xantech_sim as serial
    

# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode=async_mode)


connectedClients={}


serial_port=serial.Serial('/dev/ttyUSB0', timeout=1, baudrate=9600)

## Scss(app)
zoneArray=[]
sourceArray=[]

# playlist would come from MusicBox/MPD dynamically. Filled here only as example
playlist=[{'num':1,'name':'Brunch Cafe'},{'num':2,'name':'Classic Rock'},{'num':3,'name':'Dad Rock'},{'num':4,'name':'60s Oldies'}]

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

def readConfFile(filename):
    homepath = dirname(abspath(__file__))
    print(homepath)
    fullFilename=join(homepath,filename)
    lines = open(fullFilename).read().splitlines()
    lineArray=[];
    for lineNum,lineName in enumerate(lines,start=1):
        print str(lineNum)+': '+str(lineName)+'\r'
        lineArray.append(
            {
                'num':int(lineNum),
                'name':str(lineName)
            }
        )
        print lineArray
    return lineArray

def loadSources():
    return readConfFile('sources.txt')

def loadZones():
    # zoneArray='called func' 
    zones=readConfFile('zones.txt')
    print '**LOADING ZONES\r'
    print zones
    return zones



def left(s, amount):
    return s[:amount]

def mid(s, offset, amount):
    return s[offset:offset+amount]

def consolelog(msg):
    print msg
    return True 


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

@atexit.register
def goodbye():
    print "Exiting..."
    
@app.route('/stations')
def stations():
    print(playlist)
    return render_template('stations.html',
                           playlist=playlist,
                           async_mode=socketio.async_mode)
    
    
@app.route('/')
def index():
    stageMarkup=''
    
    print("Loading Sources...")
    sources=loadSources()
    print("Loading Zones...")
    zones=loadZones()
    zoneArray=zones
    
    print("Loading UI...")
    return render_template('dashboard-template.html',
                           sources=sources,
                           zones=zones,
                           async_mode=socketio.async_mode)
  
    
    
@socketio.on('xantech_message', namespace='/pyxantech')
def pyxantech_message(message):
    emit('xantech_response',
         {'data': message['data'] }
        )

    
@socketio.on('xantech_status', namespace='/pyxantech')
def pyxantech_chan_status(message):
    print('xantech_status')
    zone=message['zone']
    volume=message['volume']
    emit('set_status',
         {'zone':zone,
          'command':'?'+str(zone)+'ZS+',
          'status':get_zone_status(zone,volume)},
          broadcast=True)


@socketio.on('xantech_command', namespace='/pyxantech')
def pyxantech_command(message):
    # session['receive_count'] = session.get('receive_count', 0) + 1
    print message;
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


@socketio.on('disconnect_request', namespace='/pyxantech')
def disconnect_request(message):
    emit('xantech_response',
         {'data': 'Disconnected!'})
    disconnect()


@socketio.on('connect', namespace='/pyxantech')
def pyxantech_connect():
    # get status of all zones
    
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
    if clientId not in connectedClients:
        connectedClients[clientId] = time.time()
    print('* Clients remaining: '+str(len(connectedClients)))



@socketio.on('disconnect', namespace='/pyxantech')
def pyxantech_disconnect():
    clientId=request.sid
    
    print('Client disconnected', clientId)
    if clientId in connectedClients:
        del connectedClients[clientId]
    print('* Clients remaining: '+str(len(connectedClients)))


if __name__ == '__main__':
    socketio.run(app, debug=True,host='0.0.0.0')
