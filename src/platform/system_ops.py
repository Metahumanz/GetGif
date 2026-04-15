import os
import tkinter as tk
from tkinter import filedialog


class SystemOps:
    def browse_directory(self) -> str:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return filedialog.askdirectory(parent=root)
        finally:
            root.destroy()

    def open_folder(self, path: str) -> bool:
        if not path or not os.path.isdir(path):
            return False
        os.startfile(path)
        return True
