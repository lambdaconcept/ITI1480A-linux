#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 2018 - LambdaConcept
# Pierre-Olivier Vauboin <po@lambdaconcept.com>

class HookStub():
    """ All hooks should inherit from this stub class.
    """
    def push(self, name, endpoint, address, data):
        pass

    def stop(self):
        pass
