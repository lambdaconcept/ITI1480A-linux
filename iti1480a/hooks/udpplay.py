#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 2018 - LambdaConcept
# Pierre-Olivier Vauboin <po@lambdaconcept.com>

import os
import sys
import socket

from hook import HookStub

def print_data(data):
    s = ""
    for d in data:
       s += "{:02x} ".format(ord(d))
    print(s)

class Hook(HookStub):
    """ This hooks forwards all the data received on a particular endpoint via UDP.
    This can be useful for testing a protocol decoder for example.
    """
    def __init__(self, server_address, server_port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((server_address, server_port))

    def push(self, endpoint, address, data):
        if not endpoint == 4:
            return

        # Wait for trigger
        client_data, client_address = self.sock.recvfrom(1024)

        print('OUT:', len(data))
        print_data(data)

        self.sock.sendto(data, client_address)
        print()

    def stop(self):
        self.sock.close()
