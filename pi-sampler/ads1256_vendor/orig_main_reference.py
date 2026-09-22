#!/usr/bin/python
# -*- coding:utf-8 -*-


import time
import ADS1256
import RPi.GPIO as GPIO
import socket
import sys

device = socket.gethostname()


#try:
ADC = ADS1256.ADS1256()
ADC.ADS1256_init()
ADC.ADS1256_ConfigADC(0,0x82)

s = socket.socket()
s.connect(('graphite',2023))


while(1):
#        time.sleep(0.1)
        ts = time.time()
        ADC_Value = ADC.ADS1256_GetAll()
        cha = (ADC_Value[0] + ADC_Value[2] + ADC_Value[4] + ADC_Value[6]) / 4.0
        chb = (ADC_Value[1] + ADC_Value[3] + ADC_Value[5] + ADC_Value[7]) / 4.0

        s.sendall("web.stats.sensors.%s.ch1raw %f %d\n".encode() % (device.encode(), cha, ts))
        s.sendall("web.stats.sensors.%s.ch2raw %f %d\n".encode() % (device.encode(), chb, ts))
#        print ("0 ADC = %lf"%(ADC_Value[0]*5.0/0x7fffff))
#        print ("1 ADC = %lf"%(ADC_Value[1]*5.0/0x7fffff))
#        print ("2 ADC = %lf"%(ADC_Value[2]*5.0/0x7fffff))
#        print ("3 ADC = %lf"%(ADC_Value[3]*5.0/0x7fffff))
#        print ("4 ADC = %lf"%(ADC_Value[4]*5.0/0x7fffff))
#        print ("5 ADC = %lf"%(ADC_Value[5]*5.0/0x7fffff))
#        print ("6 ADC = %lf"%(ADC_Value[6]*5.0/0x7fffff))
#        print ("7 ADC = %lf"%(ADC_Value[7]*5.0/0x7fffff))
#        print ("\33[9A")

#except :
#    GPIO.cleanup()
#    print ("\r\nProgram end     ")
#    exit()
