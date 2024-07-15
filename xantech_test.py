import glob
import io
import json
import os
from os.path import dirname, abspath, join
import serial
import sys
import time

# load and store zone and source names from config json
xantech_config = {
    "system":{
        "serialport":"ttyUSB0",
        "usesimulator": False,
        "debugging": False
    },
    "zones" : [],
    "sources" : []
}

config_filename = 'config.json'
homepath = dirname(abspath(__file__))
full_filename=join(homepath,config_filename)
f = open(full_filename)
xantech_config = json.load(f)
f.close()

# bulletproof for new style config file
if not "system" in xantech_config:
    print("PLEASE UPDATE YOUR config.json FILE!")
    xantech_config["system"] = {
        "serialport":"ttyUSB0",
        "usesimulator": False,
        "debugging": False
    }
if not "serialport" in xantech_config["system"]:
    print("PLEASE UPDATE YOUR config.json FILE! serialport IS NOT DEFINED!")
    xantech_config["system"]["serialport"] = "ttyUSB0"

if not "usesimulator" in xantech_config["system"]:
    print("PLEASE UPDATE YOUR config.json FILE! usesimulator IS NOT DEFINED!")
    xantech_config["system"]["usesimulator"] = False

if not "debugging" in xantech_config["system"]:
    print("PLEASE UPDATE YOUR config.json FILE! debugging IS NOT DEFINED!")
    xantech_config["system"]["debugging"] = False

# The USB port to use on the Raspberry Pi. This can usually be left as '/dev/ttyUSB0',
# but if you have multiple devices connected to your Pi, you may need to adjust this
# value. See Raspberry Pi documentation on specifying USB ports.
ACTIVE_USBPORT="/dev/"+xantech_config["system"]["serialport"]


testSerialPorts = False
try:
    serial_port=serial.Serial(port=ACTIVE_USBPORT, baudrate=9600, bytesize=8, parity='N', stopbits=1, timeout=None, xonxoff=False, rtscts=False, write_timeout=None, dsrdtr=False )
except:
    print("The serial port "+ACTIVE_USBPORT+" could not be opened. Unfortunately, this is a fatal error. Ensure the proper serial port is specified in the config.json file, and try rebooting the pi.")
    testSerialPorts = True

if testSerialPorts:
    print(" ")
    print("Available Serial Ports:")
    ports = glob.glob('/dev/tty[A-Za-z]*')
    availablePorts = 0
    for port in ports:
        try:
            s = serial.Serial(port, baudrate=9600, bytesize=8, parity='N', stopbits=1, timeout=300, xonxoff=False, rtscts=False, write_timeout=300, dsrdtr=False )
            s.close()
            print(port[5:]+": available")
            availablePorts += 1
        except (OSError, serial.SerialException):
            print(port[5:]+": NOT available")
            pass    
    if availablePorts < 1:
        print("No serial ports vailable. Either the connection to the Xantech amp is not established, or the serial port is open in another app/process. Try rebooting the Pi.")
    else:
        print('Replace the value of "serialport" in config.json with the value of one of the available serial ports above.')
    sys.exit(0)

    
## SERIAL FUNCTIONS
# send_command() - send a command/query out to the device
def send_command(command):
    response_string=''
    response_character=''
    serial_port.flushInput()
    serial_port.flushOutput()
    serial_port.write(command.encode('utf-8'))
    running=True;
    while running:
        response_character=str(serial_port.read(1).decode('utf-8'))
        
        if response_character != '!' and response_character != '?' and response_character != '+':
            response_string+=response_character
        if (response_character == '+') or (response_string.strip()=='ERROR') or (response_string.strip()=='OK'): 
            running=False

    return response_string

# get the first enabled zone in config.json
zoneid = 0
zonestr = "0"
zonename = "Unknown"
if("zones" in xantech_config):
    for z in xantech_config["zones"]:
        if("enabled" in z and (z["enabled"] is not None) and z["enabled"]):
            if("zone" in z and z["zone"] is not None):
                zoneid = z["zone"]
                zonestr = str(z["zone"])
            if("name" in z and z["name"] is not None):
                zonename = z["name"]
            break

# get the first enabled source in config.json
sourceid = 0
sourcestr = "0"
sourcename = "Unknown"
if("sources" in xantech_config):
    for s in xantech_config["sources"]:
        if("enabled" in s and (s["enabled"] is not None) and s["enabled"]):
            if("source" in s and s["source"] is not None):
                sourceid = s["source"]
                sourcestr = str(s["source"])
            if("name" in s and s["name"] is not None):
                sourcename = s["name"]
            break


if(zoneid > 0):
    print('Getting the current status of zone '+zonestr+' "'+zonename+'": ')
    print(send_command('?'+zonestr+'ZD+')) # get the status of first avaiable zone
    print(' ')
    time.sleep(3)
    print('Powering on zone '+zonestr+' "'+zonename+'": ')
    print(send_command('!'+zonestr+'PR1+')) # power on the first avaiable zone
    print(' ')
    time.sleep(3)
    if(sourceid > 0):
        print('Setting the source of zone '+zonestr+' "'+zonename+'" to source '+sourcestr+' "'+sourcename+'" : ')
        print(send_command('!'+zonestr+'SS'+sourcestr+'+')) # set the source of first avaiable zone to first available source
        print(' ')
        time.sleep(3)
    else:
        print('No sources are configured in config.json')
        time.sleep(3)
    print('Setting volume of zone '+zonestr+' "'+zonename+'" to 8: ')
    print(send_command('!'+zonestr+'VO8+')) # set the volume of first avaiable zone to 8
    print(' ')
    time.sleep(10)
    print('Powering off zone '+zonestr+' "'+zonename+'": ')
    print(send_command('!'+zonestr+'PR0+')) # power off the first avaiable zone
else:
    print('No zones are configured in config.json')
    


