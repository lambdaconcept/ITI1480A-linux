#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 2018 - LambdaConcept
# Pierre-Olivier Vauboin <po@lambdaconcept.com>

import os
import sys
import socket

from hook import HookStub

def print_data(prefix, data):
    s = "{}({}): ".format(prefix, len(data))
    for d in data:
       s += "{:02x} ".format(ord(d))
    print(s)

class Hook(HookStub):
    """ This hooks forwards all the data received on a particular endpoint via UDP.
    This can be useful for testing a protocol decoder for example.
    """
    def __init__(self, dest_ip, dest_port, epfilter):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((dest_ip, dest_port))
        self.epfilter = epfilter

    def push(self, name, endpoint, address, data):
        if not (address, endpoint) in self.epfilter:
            return

        if name == "OUT":
            print_data("TCP OUT:", data)
            self.sock.send(data)

        elif name == "IN":
            datarecv = self.sock.recv(1024)
            print_data("TCP IN:", datarecv)

    def stop(self):
        self.sock.close()
