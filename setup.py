#!/usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import setup, find_packages


PACKAGE = 'trac2kanban'
VERSION = '0.1'


setup(
    name=PACKAGE,
    version=VERSION,
    description='Copy Trac tickets to Kanban board.',
    packages=find_packages(),
    entry_points = {
        'trac.plugins': ['trac2kanban = trac2kanban'],
        },
    )
