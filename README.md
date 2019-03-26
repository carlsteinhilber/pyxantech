# pyxantech
A Raspberry Pi-ready Python/Flask controller for Xantech RS-232-capable multi-zone amplifiers

## What's a Xantech, anyway?
The MRAUDIO8X8M / MRC88 / MRC88CTL are eight-source, eight-zone distribution amplifiers produced by Xantech in the early-to-mid 2000's. They were installed in many nightclubs and high-end residential homes. The products were typically controlled by one or more wall-mounted keypads in each zone, which were programmed via Xantech's expensive (and somewhat clumbsy) *Dragon Drop* software which was a combined IDE and proprietary programming language. As wall-mounted keypads fell out-of-favor, and products like SONOS came on the market, these older Xantech products were cast aside despite their superior discreet amplifiers and multi-zone capabilities.

## So why spend any more ergs on them now?
Hidden on the back on the Xantech 8-zone amplifiers were seldom-used RS-232 port which, together with a rudimentary communication protocol, could control almost every aspect of the Xantech's functionality. Today, we can reinvigorate these old products by controlling them through a Raspberry Pi (or possibly Arduino) connected to this serial port, using this Python Flask application that can present a web-based interface to any browser-capable mobile device or computer. With Xantech 8-zone amps available on eBay, occassionally for less than even $100, this makes the PyXantech system one of the more affordable options for whole-house audio distribution.

## Features:
- Web-based, mobile-responsive controller interface
- Status syncs across all devices via web sockets
- Easy to configure zone and source names
- Control on/off, source and volume of each zone
- Xantech simulator included for testing*

## Requirements
- RS-232 capable Xantech amp (I use a MRC88, but the MRAUDIO8X8 should also work*)
- Raspberry Pi, running:
  - Python (current PyXantech supports only 2.7)
  - Flask (http://flask.pocoo.org/)
  - flask_socketio (https://pypi.org/project/Flask-SocketIO/)
  - eventlet (optional, but recommended) (https://pypi.org/project/eventlet/)
  - PySerial (https://pythonhosted.org/pyserial/)
  - PyXantech (this project)
- USB-to-serial (9-pin RS-232) adapter/dongle (something like this https://www.amazon.com/Sabrent-Converter-Prolific-Chipset-CB-DB9P/dp/B00IDSM6BW/ref=sr_1_1_sspa)

## Set-up
- Install Raspberry Pi OS (I usually use *Raspbian Stretch Lite* - no desktop needed)
- Connect the Xantech serial port to any USB port on the Raspberry Pi using an adapter
  - (if you wish to run the app on a computer without a Xantech connected, see *Testing* below)
- Power on the Pi and connect it to the network (hard-wired is recommended)
- Enable SSH server and shell into Raspberry Pi
- Python 2.7 should be installed by default in Raspbian. If not, install it.
- Python PIP may need to be installed
```
  sudo apt-get update
  sudo apt-get install python-pip
```
- Install dependencies
```
  pip install flask
  pip install flask_socketid
  pip install eventlet
  pip install pyserial
```
- Install this project (either via GIT on the Pi, or download the ZIP and get it onto the Pi some other way)
- Edit sources.txt
  - Put the name of one source per line (source 1 on line 1, source 2 on line 2, and so on... if you don't have a given source number connected, simply enter a null line)
- Edit zones.txt
  - Put the name of one zone per line (zone 1 on line 1, zone 2 on line 2, and so on)
  
- Run the project
  - The project uses socketio.run(), so the typical `flask run` will likely cause issues. Use instead:
```
        python app.py &
```
- Open a web browser on a device on the same network as your Pi, and point it to port 5000 on the IP address of the Pi (for example: http://192.168.1.100:5000)
  - If you wish to control your Xantech from a device on *any* network (ie - your mobile data plan, or external computer), you'll need to set up dynamic DNS, firewall rules, etc... I'll leave that up to you.
  
- Click the power button for any zone, select a source, set the volume, and enjoy.


## Testing
Included with the PyXantech project is a Xantech serial simulator. This allows the project to run (test) even if the Xantech amplifier is not connected (even on non-Pi computers)
- edit *app.py* and change
```
  ACTIVE_SERIAL=True
```
to
```
  ACTIVE_SERIAL=**False**
```
- configure source and zones, and run app as above
    
    

 


