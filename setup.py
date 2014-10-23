#!/usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

setup(
    name="kizkiz",
    version="0.1.0",
    description="An unofficial app for the Parrot Zik",
    url="http://github.com/TkTech/kizkiz",
    author="Tyler Kennedy",
    author_email="tk@tkte.ch",
    classifiers=[
        "Programming Language :: Python",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 3 - Alpha",
    ],
    packages=find_packages(),
    install_requires=[
        'docopt',
        'rumps',
        'xmltodict'
    ],
    extras_require={
        'docs': [
            'sphinx',
            'sphinx_rtd_theme',
            'ghp-import'
        ]
    },
    options={
        'py2app': {
            'argv_emulation': True,
            'plist': {
                'LSUIElement': True
            },
            'packages': [
                'rumps'
            ]
        }
    },
    app=[
        'kizkiz/app.py'
    ]
)
