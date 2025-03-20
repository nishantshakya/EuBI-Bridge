# -*- coding: utf-8 -*-
"""
@author: bugra
"""

import setuptools

def parse_requirements(filename):
    with open(filename, encoding='utf-8') as fid:
        requires = [line.strip() for line in fid.readlines() if line]
    return requires

def readme():
   with open('README.txt') as f:
       return f.read()

requirements = parse_requirements('requirements.txt')

packages = setuptools.find_packages()
print(f'packages are: {packages}')

setuptools.setup(
    name = 'eubi_bridge',
    version = '0.0.1',
    author = 'Bugra Özdemir',
    author_email = 'bugraa.ozdemir@gmail.com',
    description = 'A package for converting datasets to OME-Zarr format.',
    long_description = readme(),
    long_description_content_type = "text/markdown",
    url = 'https://github.com/Euro-BioImaging/EuBI-Bridge',
    license = 'MIT',
    packages = setuptools.find_packages(),
    include_package_data=True,
    install_requires = requirements,
    entry_points={'console_scripts': [
                "eubi = eubi_bridge.cmd:eubibridge_cmd"
            ]
        }
    )
