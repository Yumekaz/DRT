"""
DRT Command Line Interface

Usage:
    python -m drt dump <log_file>    Dump log contents
    python -m drt info <log_file>    Show log information
"""

from .runtime import main

if __name__ == '__main__':
    main()
