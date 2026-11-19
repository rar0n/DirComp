# DirComp - Directory Compare and Duplicate Finder

## Two modes
- Compare Folders  (two folders side-by-side)
- Duplicate Finder (find duplicates in same folder or sub folders)


WIP! (Work in progress)

### Compare Folders:

<img width="1671" height="1248" alt="image" src="https://github.com/user-attachments/assets/4256e895-f97d-4c76-ba79-cba630361caa" />


### Duplicate Finder:

<img width="1671" height="1248" alt="image" src="https://github.com/user-attachments/assets/0c0e332e-5851-4411-9917-99f6b1b6b2ae" />


## Features

Basically
 - Two modes
   - Compare two folders side-by-side / manually synchronize their contents
   - Find duplicate files, display duplicates in right-side view
 - Both modes compare files based on
   - Filename, size and date
   - File extension(s) optional
   - Hash value
   - Fingerprint (quick hash beginning / end of file + filesize)
 - Two views
   - Tree view of files and folders (File List View)
   - Statistics view, barchart timeline view of file dates or file sizes
 - Color coded highlights
 - Select files based on time or size in Statistics View
 - Synchronize selection of both panes (if the corresponding item (files or folders) exists in the other pane)
 - Pastes multi-selected files from one pane and automatically re-create their folder structure (copying their relative path)
 - Symmetrical (ish) GUI, or not, depending on settings.
 - Color customization (theme colors, custom colors for most buttons etc.)
 - Cached folders
 - Manual Snapshop of folders (see what was there, can also be used for comparisons, selections)


### Road map / planned wish list / Maybe

 - Layout tweaks?
 - Other?

### Vibe coded

Yes. Full disclosure: I don't know Python that well. Claude does though :-)

95 % vibe coded with Claude 5 free over a couple of months. This is not a one-shot slop (if I may say so myself). I'm happy with how this has turned out so far, and how "we" (me and Claude) have worked through some Python and PySide6 quirks and shortcomings.

The other 5% (ish) is also vibe coded while having a subscription (for now), Claude 5.5.

Note: Most of the source code comments is Claude's. Some (not all) of the
      reasoning behind _why_ something is done is Claude's assumption and
      a bit off. But a lot is on-point as well!


## Install

Requires Python 3.10 or newer (I think)

### Linux

1. Save/extract DirComp into a folder, `cd` to that folder or just
   right-click the folder and select "Open in Terminal".
2. Install dependencies into a venv (virtual environment):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

### Windows

1. Install [Python](https://www.python.org/downloads/windows/) if you haven't.
   In the installer, tick **"Add python.exe to PATH"**.
2. Save/extract DirComp into a folder. In File Explorer, open the folder,
   type `cmd` (or `powershell`) in the address bar and press Enter, to open a
   terminal there.
3. Create the venv and install dependencies. The venv is created the same way
   in both shells, only activating it differs:

   **Command Prompt:**
   ```bat
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

   **PowerShell:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   (If PowerShell refuses to run the activate script with an error about
   execution policies, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
   once, or just use Command Prompt.)

   If `python` isn't found, try `py -m venv venv` instead.

(The venv prompt shows `(venv)` when it's active. Activate it again in each new terminal.)


## Run program

Run it by starting `main.py` from its folder, in a terminal.
You must activate the virtual environment (venv) first:

Linux:
```bash
source venv/bin/activate
python3 main.py
```
Windows (Command Prompt):
```bat
venv\Scripts\activate
python main.py
```
Windows (PowerShell):
```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

## Usage
(Most remains to be written as of now. Atm these short instructions will have to do)

For starters I suggest making 2 dummy test folders with unimportant files and folders in them, some of which are copies of each other's contents. Then just play with the software to see how it works.

Basically, you set two "root" folders, one in each pane (left / right pane) view, and can compare their contents, copy / move files or folders between them, etc.

### Settings tab

Sets Theme, lots of custom colors for highigths and other stuff.

#### Global Settings

##### Options

###### Comparison type
 - Name, size and date
 - Hash value (A few different to choose between)
 - Fingerprint

Name, size and date is the most basic method. It might mistake same-named files for the same file (but shows date or size difference). It might be sufficient, depending on use case.

Hash value is the slowest by far initially as it needs to hash the complete files.

Fingerprint is a shortened hash value, very fast but it's only hashing the file size and start and end chunks of a file (conceivably it could be altered in the middle while still having the same file size and fingerprint value, and a difference will be unnoticed and ignored as such).


