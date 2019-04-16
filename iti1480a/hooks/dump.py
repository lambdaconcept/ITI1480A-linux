#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 2018 - LambdaConcept
# Pierre-Olivier Vauboin <po@lambdaconcept.com>

from hook import HookStub

class Hook(HookStub):
    """ This hook simply dumps all the data received on a particular endpoint to a file.
    This can be useful for analysis or reconstructing transfered files.
    """
    def __init__(self, filename, epfilter):
        """ epfilter: should be a python list containing endpoints to dump.
        """
        self.f = open(filename, "w")
        self.epfilter = epfilter

    def push(self, endpoint, data):
        if not endpoint in self.epfilter:
            return

        self.f.write(data)

    def stop(self):
        self.f.close()
