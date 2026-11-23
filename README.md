# DirComp - Directory Compare and Duplicate Finder

## Two modes
- Compare Folders  (two folders side-by-side)
- Duplicate Finder (find duplicates in same folder or sub folders)


WIP! (Work in progress)

### Compare Folders:

<img width="1675" height="1181" alt="image" src="https://github.com/user-attachments/assets/584abe04-8bea-49bf-8fcd-4bac5a41b922" />


### Duplicate Finder:

<img width="1675" height="1181" alt="image" src="https://github.com/user-attachments/assets/af58b448-17be-401c-865f-9d4f0a1afe96" />


## Features (WIP)

 - Two main modes (central buttons at the top of Main tab)
   - Compare Folders
     - Compare two folders side-by-side (manually synchronize their contents)
   - Duplicate finder
     - Re-use right-side pane to display duplicate files instead of another folder.
     - Two view modes
       - Tree structure: Show duplicate files inside their parent folders
       - Flat list: Only show duplicate files and a duplicate count.
     - Selecting duplicates on the right pane also selects all duplicates in the left pane
 - Compare files based on (both modes)
   - Filename, size and / or date
     - Optional File extension.
     - Optional only first part of filename (disregard multiple file extensions)
   - Hash value
   - Fingerprint (quick hash beginning / end of file + filesize)
     - Setable start / end hash chunk size
 - Two views (both modes)
   - Tree view of files and folders (File List View)
   - Statistics view (bar charts)
     - Select files or folders via (multi) selecting bars or a range of bars
     - Unselect all files or folders by clicking in an empty area inside Statistics View
     - Two modes in Statistics View
       - Files
       - Folders
     - Bar chart overview of file
       - Timeline
       - File sizes
       - File Filter entries (E.G. file extension statistics counts)
 - Color coded for at-a-glance for which files are missing from either side
 - Synchronize selection of both panes (if the corresponding item (files or folders) exists in the other pane)
 - Pastes multi-selected files from one pane and automatically re-create their folder structure (copying their relative path)
 - Symmetrical (ish) GUI, or not, depending on settings.
 - Color customization (theme colors, custom colors for most buttons etc.)
 - Cached folders
 - Manual Snapshop of folders (see what was there, can also be used for comparisons, selections)

### Nice-to-have
 - Save selected item list as plain text


### Road map / planned wish list / Maybe

 - Layout tweaks?
 - Other?

### Vibe coded

Yes. Full disclosure: I don't know Python that well. Claude does though :-)

Started with Claude 5 free over a couple of months, continued for a coupe more with a sub, mostly Claude Sonnet 5.5 at medium effort. This is not a one-shot slop (if I may say so myself). I'm happy with how this has turned out so far.

Note: Most of the source code comments is Claude's. Some (not all) of the
      reasoning behind _why_ something is done is Claude's assumption and
      _may_ be a bit off. But a lot is on-point as well!


## Install

Requires Python 3.10 or newer.

### Getting the program

Either download the repository as a ZIP file from the GitHub page
(green **Code** button, then **Download ZIP**) and extract it into a folder,
or use `git` to clone it into a local folder:

```bash
git clone https://github.com/rar0n/DirComp.git
cd DirComp
```

With a clone, `git pull` later fetches updates.




### Linux

1. Get the program into a folder (see above). `cd` to that folder or just
   right-click the folder and select "Open in Terminal".
2. Install dependencies into a venv (virtual environment):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   (If `python3 -m venv` complains about `ensurepip`, you may need
   `sudo apt install python3-venv`.)

### Windows

1. Install [Python](https://www.python.org/downloads/windows/) if you haven't.
   In the installer, tick **"Add python.exe to PATH"**.
2. Get the program into a folder (see above). In File Explorer, open the folder,
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


## Usage

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

## Instructions
(Very much missing atm, sorry!  Atm these short instructions will have to do)

So for now I will simply suggest that you make 2 dummy test folders with unimportant files and folders in them, some of which are copies of each other's contents. Then just play with the software to see how it works.


### **IMPORTANT NOTE !**
**Use this at your own risk!**
I take no responsibility if you delete any files or folders you didn't want to, or otherwise mess up anything using this program!
Back up your files! Also your folders!

### NOTE!
Most file listings depend on:
- Global File Filter filtering
- Recursive Folder Selections setting (whether or not to scan inside non-expanded folders in the upper view)


### Compare Folders

Basically, you set two "root" folders, one in each pane (left / right pane) view, and can compare their contents, copy / move files or folders between them, etc.

The entries in the drop-down history list above the file view can be deleted by hovering over them and press "DEL", or "Clear History" button under "Global Settings" - "Options", in Settings tab.

You can also save a folder in a "Snapshot" file (similar to a cache), and later load it (To e.g. compare differences, but ofc any cut/copy/paste to/from a Snapshot isn't possible)

### Duplicate Finder

The other main mode. The right side pane is re-used, listing duplicates found in the left pane's folder..




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


