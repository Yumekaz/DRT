#!/usr/bin/env python3
"""
Setup script for DRT - Deterministic Record-and-Replay Runtime
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_path = Path(__file__).parent / 'README.md'
long_description = readme_path.read_text(encoding='utf-8') if readme_path.exists() else ''

setup(
    name='drt',
    version='0.3.0',
    author='Yumekaz',
    description='Deterministic record-and-replay runtime for DRT-instrumented Python concurrency code',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/Yumekaz/DRT',
    project_urls={
        'Source': 'https://github.com/Yumekaz/DRT',
        'Documentation': 'https://github.com/Yumekaz/DRT/tree/main/docs',
        'Issues': 'https://github.com/Yumekaz/DRT/issues',
    },
    packages=find_packages(exclude=('tests', 'tests.*', 'demo', 'demo.*')),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Debuggers',
        'Topic :: Software Development :: Testing',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.9',
    install_requires=[],
    extras_require={
        'dev': [
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
