import serial

# USB-serial dongle
ACTIVE_USBPORT="/dev/ttyUSB0"

serial_port=serial.Serial(ACTIVE_USBPORT, timeout=1, baudrate=9600)

## SERIAL FUNCTIONS
# send_command() - send a command/query out to the device
def send_command(command):
    response_string=''
    response_character=''
    serial_port.flushInput()
    serial_port.flushOutput()
    serial_port.write(command.encode())
    running=True;
    while running:
        response_character=str(serial_port.read(1).decode())
        
        if response_character != '!' and response_character != '?' and response_character != '+':
            response_string+=response_character
        if (response_character == '+') or (response_string.strip()=='ERROR') or (response_string.strip()=='OK'): 
            running=False
    print(response_string)
    return response_string

    
send_command('?1ZD+') # get the status of zone 1
# send_command('!1PT+') # toggle zone 1 on/off
# send_command('!1VO18+') # set the volume for zone 1 at 18 (approx half loudness)
# send_command('!1SS1+') # set the source for zone 1 to source 1

