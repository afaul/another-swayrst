from tkinter import *
from tkinter import ttk

NAME = "col 4; row 4"

root = Tk()
root.title(NAME)
frm = ttk.Frame(root, padding=10)
frm.grid()
ttk.Label(frm, text=NAME).grid(column=0, row=0)
ttk.Button(frm, text="Quit", command=root.destroy).grid(column=1, row=1)
root.mainloop()
