#!/usr/bin/env python3
"""
Setup script for DRT - Deterministic Record-and-Replay Runtime
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_path = Path(__file__).parent / 'README.md'
long_description = readme_path.read_text() if readme_path.exists() else ''

setup(
    name='drt',
    version='1.0.0',
    author='DRT Project',
    author_email='drt@example.com',
    description='Deterministic Record-and-Replay Runtime for Python',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/example/drt',
    packages=find_packages(),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Debuggers',
        'Topic :: Software Development :: Testing',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.8',
    install_requires=[
        # No external dependencies - uses only stdlib
    ],
    extras_require={
        'dev': [
            'pytest>=7.0',
            'pytest-cov>=4.0',
            'mypy>=1.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'drt=drt.runtime:main',
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
