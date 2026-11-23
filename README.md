# DirComp - Directory Compare and Duplicate Finder

## Two modes
- Compare Folders  (two folders side-by-side)
- Duplicate Finder (find duplicates in same folder or sub folders)


WIP! (Work in progress)

## **IMPORTANT NOTE !**
**Use this at your own risk!**
I take no responsibility if you delete any files or folders you didn't want to, or otherwise mess up anything using this program!
Back up your files! Also your folders!


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
 - Synchronize Views (Same selection in both panes, if the corresponding item (file or folder) exists in the other pane)
 - Pastes multi-selected files from one pane and automatically re-create their folder structure (copying their relative path)
 - Symmetrical (ish) GUI, or not, depending on settings.
 - Color customization (theme colors, custom colors for most buttons etc.)
 - Cached folders
 - Manual Snapshop of folders (see what was there, can also be used for comparisons, selections)

### Nice-to-have
 - Save selected items as a plain text list


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


### **IMPORTANT NOTE ! (Again)**
**Use this at your own risk!**
I take no responsibility if you delete any files or folders you didn't want to, or otherwise mess up anything using this program!
Back up your files! Also your folders!

### NOTE!
Most file listings depend on:
- Global File Filter filtering
- Recursive Folder Selections setting (whether or not to scan inside non-expanded folders in the upper view)


## Main modes

### Compare Folders
Basically, you set two "root" folders, one in each pane (left / right pane) view, and can compare their contents, copy / move files or folders between them, etc.

The entries in the drop-down history list above the file view can be deleted by hovering over them and press "DEL", or "Clear History" button under "Global Settings" - "Options", in Settings tab.

You can also save a folder in a "Snapshot" file (similar to a cache), and later load it (To e.g. compare differences, but ofc any cut/copy/paste to/from a Snapshot isn't possible)


### Duplicate Finder

The other main mode. The right side pane is re-used, listing duplicates found in the left pane's folder..




## Buttons
The buttons at the bottom part is divided into a few groups
- Select
- File Op
- Clipboard
- Both Panes (central group applying to both panes)


### Select buttons

Most are **additive**, each select button adds to the current selection.
If you want to select from a "blank slate" (nothing selected at first), then press Clear (in this group) first!
This group differs slightly in the two main modes:

Compare Folders mode
- Missing (Select files that exist here but are missing on the other pane)
- Matching (Selelct files that exists in both panes)
- Newer (Files exist in both panes but this pane's is newer)
- Older (As above but this is older)
- Smaller (Files exists in both panes but this has a smaller filesize)
- Larger (As above except this side has the larger filesize)
It is implied that the files are compared to the same relative locations on the other pane / folder.

Duplicate Finder mode
- Duplicates (Select all files that are duplicates of each other)
- Extra copies (As above, except un-select 1 copy of each duplicate set, so as to not delete all of them for example)
- Sel. Dups (From a selection, select all files that have duplicates, ignoring un-selected items)
- Sel. Extras (As above, but leave 1 file in each duplicate set un-selected)

Common:
Your basic Select All, Clear, Invert (selection) and a couple of auxillaries:
- All Parents - Select an items parents all the way up to the top of the pane's root folder
- All Childs - The reverse of the above, selects everything inside a folder
- Item Parent - Select an item's (file or folder) parent folder
- Item Child - Select a folders content, but only one level down


### Clipboard buttons
There's the basic cut/copy/paste, except it's not as basic perhaps, as it will re-create any parent folders of any selected item that is pasted.
So if you select a file or folder deeply nested, it will paste the item in the same deeply nested folder structure, re-creating it.
Exception is if you paste in an ordinary file explorer instead of in DirComp.
Other buttons:
- Paste Folders - as the name implies, pastes only folders of the selection, ignoring the files
- Paste Flat - Pastes everything in one folder (ignoring the folder structure)
- Move--> - Simply moves the selection to the other pane
- Copy--> - As above but copies it

### File Op
Self explanatory, rudimentary implemented for now.

### Both Panes buttons
Just two functions for now:
- Sync - A powerful button, be very careful!! That is, be **SURE** that the left and right folders should be copies of each other! Newest file wins if the same file exists in both panes.
- Swap - Just swaps left and right. If done in "Duplicate Finder", swaps the hidden right side with the visible left, for finding duplicate files.





## Settings tab
Sets Theme, lots of custom colors for highigths and other stuff. Basically a zillion settings there now :P (Lots of colors and theming, but not everything)


### Comparison Options
(Settings tab, Global Settngs)

Which files are considered "counterparts" of each other is set here.
- Name, size and date - they all must match
- Name and size
- Name
  - With any of the Name types, you may ignore file extension(s).
- Hash value (Slow, a selection of hashing algorithms available. Most accurate)
  - Hash value is the slowest by far initially as it needs to hash the complete files.
- Fingerprint (Quickly hash only a subset + file size)

Name, size and date is the most basic method. It might mistake same-named files for the same file (but shows date or size difference). It might be sufficient, depending on use case.
Perhaps you only want a visual comparison of file sets that differs only with file extensions, for example.

Fingerprint is a shortened hash value, very fast but it's only hashing the file size and start and end chunks of a file (conceivably it could be altered in the middle while still having the same file size and fingerprint value, and a difference will be unnoticed and ignored as such).





