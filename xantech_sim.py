#!/usr/bin/env python
# PROJECT: PyXantech
# A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers
# by ProfessorC  (professorc@gmail.com)
# https://github.com/grandexperiments/pyxantech
# FILE: xantech-sim.py - to simulate the Xantech serial connection
# based on the PySerial Simulator by D. Thiebaut
# http://www.science.smith.edu/dftwiki/index.php/PySerial_Simulator

'''
    v.1.0 - Inital release  2019/03/25 - GNU General Public License (GPL 2.0)
    v.2.0 - Python 3/Pico release  2023/10/25 - GNU General Public License (GPL 2.0)
'''

'''
    Receives Xantech commands, and returns Xantech query results as if the Xantech were connected
    to the serial/USB port. Some commands are not yet supported - notably the subzone commands - since
    I don't have a setup available to test. Feel free to fill them in if you need them.

    It also does not handle zone metadata
'''



# set up a dict of Xantech settings for each zone (if more than one Xantech is used, you'll want to add elements)
# using 1-based indexing, just because
xantech=[
    { 'PR':0, 'SS':0, 'VO':0, 'MU':0, 'TR':0, 'BS':0, 'BA':0, 'LS':0, 'PS':0 },   # throw away zone, will never be used
    { 'PR':1, 'SS':1, 'VO':14, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 }, # zone 1
    { 'PR':0, 'SS':2, 'VO':3, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 },  # zone 2
    { 'PR':0, 'SS':3, 'VO':5, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 },  # zone 3
    { 'PR':0, 'SS':1, 'VO':8, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 },  # zone 4
    { 'PR':0, 'SS':2, 'VO':8, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 },  # zone 5
    { 'PR':0, 'SS':3, 'VO':8, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 },  # zone 6
    { 'PR':0, 'SS':1, 'VO':8, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 },  # zone 7
    { 'PR':0, 'SS':1, 'VO':8, 'MU':0, 'TR':7, 'BS':7, 'BA':32, 'LS':0, 'PS':0 }   # zone 8
]

# set up an integer toggle
toggleOnOff=[1,0]


def parseIncomingCommand(cmd):
    cmdIn=str(cmd)
    print("parseIncomingCommand cmd: " + cmdIn)

    # is command a command or a query
    cmdMode=cmdIn[0]
    cmdIn=cmdIn[1:]

    # is next character a zone
    cmdZone=0
    cmdZoneStr=""
    if(str(cmdIn[0]).isdigit()):
        cmdZoneStr=cmdIn[0]
        cmdZone=int(cmdZoneStr)
        cmdIn=cmdIn[1:]

    # next two characters should always be the command action
    cmdAction=cmdIn[:2]
    cmdIn=cmdIn[2:]

    print("parseIncomingCommand cmdMode: " + str(cmdMode))
    print("parseIncomingCommand cmdIn: " + str(cmdIn))
    print("parseIncomingCommand cmdZone: " + str(cmdZone))
    print("parseIncomingCommand cmdZoneStr: " + str(cmdZoneStr))
    print("parseIncomingCommand cmdAction: " + str(cmdAction))

    notsupportedyet=False

    result=""
    if(cmdMode=="!"):
        # command was an instruction

        # get everything remaining minus the '+'
        cmdIn=cmdIn[:-1]
        value=0

        # if the remainder is numeric, make it an integer
        if(cmdIn.isdigit()):
            value=int(cmdIn)


        # we could combine everything into 3 conditions, but I'm keeping them
        # separated out for readibility
        # REALLY wish Python had a case statement!
        if(cmdAction=="AO"):
            # All Zones Off
            # loop through all zones and set PR to 0
            i = 0
            while i < len(xantech):
                xantech[i]["PR"]=0
                i += 1
        elif(cmdAction=="PR"):
            # set power for zone to mode
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="PT"):
            # toggle power for zone
            xantech[cmdZone]['PR']=toggleOnOff[xantech[cmdZone]['PR']]
        elif(cmdAction=="SS"):
            # set source for zone to value
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="VO"):
            # set volume for zone to value
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="VI"):
            # increment volume for zone
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]+1
        elif(cmdAction=="VD"):
            # decrement volume for zone
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]-1
        elif(cmdAction=="VZ"):
            # set volume for subzone to value
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="VX"):
            # increment volume for subzone
            notsupportedyet=True
        elif(cmdAction=="VY"):
            # decrement volume for subzone
            notsupportedyet=True
        elif(cmdAction=="MU"):
            # set mute for zone to mode
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="MT"):
            # toggle mute for zone
            xantech[cmdZone]['MU']=toggleOnOff[xantech[cmdZone]['MU']]
        elif(cmdAction=="MZ"):
            # set mute for subzone to mode
            # xantech[cmdZone][cmdAction]=value
            notsupportedyet=True
        elif(cmdAction=="MX"):
            # toggle mute for subzone
            notsupportedyet=True
        elif(cmdAction=="TR"):
            # set treble for zone to value
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="TI"):
            # increment treble for zone
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]+1
        elif(cmdAction=="TD"):
            # decrement treble for zone
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]-1
        elif(cmdAction=="BS"):
            # set bass for zone to value
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="BI"):
            # increment bass for zone
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]+1
        elif(cmdAction=="BD"):
            # decrement bass for zone
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]-1
        elif(cmdAction=="BA"):
            # set balance for zone to value
            xantech[cmdZone][cmdAction]=value
        elif(cmdAction=="BL"):
            # step balance for zone to left
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]+1
        elif(cmdAction=="BR"):
            # step balance for zone to right
            xantech[cmdZone][cmdAction]=xantech[cmdZone][cmdAction]-1
        elif(cmdAction=="MC"):
            # execute macro number (zone = macro #)
            notsupportedyet=True
        elif(cmdAction=="MK"):
            # execute keyboard button
            notsupportedyet=True
        elif(cmdAction=="ZA"):
            # zone activity auto update
            notsupportedyet=True
        elif(cmdAction=="ZP"):
            # zone periodic auto update
            notsupportedyet=True
        else:
            notsupportedyet=True

        if(notsupportedyet):
            # just a nicety in case we receive a command we don't support/understand
            result="!ERROR+"
        else:
            # don't remember the actual return value... could be just "OK", or "OK+",
            # but it doesn't matter because the reader in app.py strips off !'s and +'s anyway
            result="!OK+"

    elif(cmdMode=="?"):
        # we could combine everything into 3 conditions, but I'm keeping them
        # separated out for readibility
        if(cmdAction=="PR"):
            # get power setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="SS"):
            # get source setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="VO"):
            # get volume setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="VX"):
            # get volume setting for subzone
            notsupported=True
        elif(cmdAction=="MU"):
            # get mute setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="MX"):
            # get mute setting for subzone
            notsupported=True
        elif(cmdAction=="TR"):
            # get treble setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="BS"):
            # get bass setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="BA"):
            # get balance setting for zone
            result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
        elif(cmdAction=="CS"):
            # get current sense setting for zone
            # result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
            notsupported=True
        elif(cmdAction=="VS"):
            # get video sense setting for zone
            # result="#"+cmdZoneStr+" "+cmdAction+str(xantech[cmdZone][cmdAction])+"+"
            notsupported=True
        elif(cmdAction=="ZA"):
            # get zone activity auto update setting for zone
            notsupported=True
        elif(cmdAction=="ZP"):
            # get zone periodic auto update setting for zone
            notsupported=True
        elif(cmdAction=="ZS"):
            # get status of zone
            result="#"+cmdZoneStr+"ZS PR"+str(xantech[cmdZone]['PR'])+" SS"+str(xantech[cmdZone]['SS'])+" VO"+str(xantech[cmdZone]['VO'])+" MU"+str(xantech[cmdZone]['MU'])+" TR"+str(xantech[cmdZone]['TR'])+" BS"+str(xantech[cmdZone]['BS'])+" BA"+str(xantech[cmdZone]['BA'])+" LS"+str(xantech[cmdZone]['LS'])+" PS"+str(xantech[cmdZone]['PS'])+"+"
        elif(cmdAction=="ZD"):
            # get data of zone
            result="#"+cmdZoneStr+"ZS PR"+str(xantech[cmdZone]['PR'])+" SS"+str(xantech[cmdZone]['SS'])+" VO"+str(xantech[cmdZone]['VO'])+" MU"+str(xantech[cmdZone]['MU'])+" TR"+str(xantech[cmdZone]['TR'])+" BS"+str(xantech[cmdZone]['BS'])+" BA"+str(xantech[cmdZone]['BA'])+" LS"+str(xantech[cmdZone]['LS'])+" PS"+str(xantech[cmdZone]['PS'])+"+"
        elif(cmdAction=="ZM"):
            # get metadata of zone
            notsupported=True
        else:
            notsupportedyet=True

        if(notsupportedyet):
            # just a nicety in case we receive a command we don't support/understand
            result="!ERROR+"



    print(result)
    return result



# a Serial class emulator
class Serial:

    ## init(): the constructor.  Many of the arguments have default values
    # and can be skipped when calling the constructor.
    def __init__( self, port='COM1', baudrate = 19200, timeout=1,
                  bytesize = 8, parity = 'N', stopbits = 1, xonxoff=0,
                  rtscts = 0):
        self.name     = port
        self.port     = port
        self.timeout  = timeout
        self.parity   = parity
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.stopbits = stopbits
        self.xonxoff  = xonxoff
        self.rtscts   = rtscts
        self._isOpen  = True
        self._receivedData = ""
        self._data = "READY\n"

    ## isOpen()
    # returns True if the port to the Xantech is open.  False otherwise
    def isOpen( self ):
        return self._isOpen

    ## open()
    # opens the port
    def open( self ):
        self._isOpen = True

    ## close()
    # closes the port
    def close( self ):
        self._isOpen = False

    ## write()
    # writes a string of characters to the Xantech
    def write( self, string ):
        strReceived = str(string.decode())
        print( 'Xantech received: "' + strReceived + '"' )
        self._receivedData = strReceived
        # parse out string into a Xantech command
        self._data=parseIncomingCommand(strReceived)

    ## read()
    # reads n characters from the fake Xantech. Actually n characters
    # are read from the string _data and returned to the caller.
    def read( self, n=1 ):
        s = self._data[0:n]
        self._data = self._data[n:]
        #print( "read: now self._data = ", self._data )
        return s

    ## readline()
    # reads characters from the fake Xantech until a \n is found.
    def readline( self ):
        returnIndex = self._data.index( "\n" )
        if returnIndex != -1:
            s = self._data[0:returnIndex+1]
            self._data = self._data[returnIndex+1:]
            return s
        else:
            return ""

    ## flushInput()
    # clear the incoming "buffer"
    # included here for completeness
    def flushInput(self):
        self._receivedData=""

    ## flushOutput()
    # clear the outgoing "buffer"
    # included here for completeness
    def flushOutput(self):
        self._data=""

    ## __str__()
    # returns a string representation of the serial class
    def __str__( self ):
        return  "Serial<id=0xa81c10, open=%s>( port='%s', baudrate=%d," \
               % ( str(self.isOpen), self.port, self.baudrate ) \
               + " bytesize=%d, parity='%s', stopbits=%d, xonxoff=%d, rtscts=%d)"\
               % ( self.bytesize, self.parity, self.stopbits, self.xonxoff,
                   self.rtscts )

