"""Shim so Deluge's egg-based plugin install can use ``setup.py bdist_egg``."""

from setuptools import setup

setup(
    author="Gabor Deli",
    author_email="gabor.deli@gmail.com",
    url="https://cirice-gitlab.ddns.net/gabor/diskguard",
    platforms=["any"],
)
