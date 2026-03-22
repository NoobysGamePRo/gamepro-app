"""
Noobys GamePRo — main entry point.

Run this file to launch the GamePRo controller GUI:
    python main.py

Or use the compiled executable (see build.bat).
"""

import sys
import os

# Ensure the project root is on sys.path so all imports work correctly,
# both when running from source and when compiled by PyInstaller.
if getattr(sys, 'frozen', False):
    # Running as a PyInstaller bundle — _MEIPASS is the temp extraction dir
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from gui.app import GameProApp


def main():
    app = GameProApp()
    app.mainloop()


if __name__ == '__main__':
    main()
