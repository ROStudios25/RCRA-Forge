"""
RCRA Forge - Ratchet & Clank: Rift Apart Level Editor & Asset Exporter
Entry point
"""

import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RCRA Forge")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("RCRA Community")
    # HiDPI is always enabled in PyQt6 6.0+ — no setAttribute needed

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
