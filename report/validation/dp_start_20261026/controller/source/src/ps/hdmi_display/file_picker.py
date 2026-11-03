"""Bounded, read-only local media browsing; no recursive scans or deletion."""
from pathlib import Path


class VideoPicker:
    PAGE_SIZE = 7
    LIMIT = 200

    def __init__(self, roots):
        self.roots = [Path(p) for p in roots]
        self.directory = self.root = None
        self.entries = []
        self.page = 0
        self.message = ''
        self.home()

    def home(self):
        self.directory = self.root = None
        self.entries = list(self.roots)
        self.page = 0
        self.message = 'Choose a local media folder'

    @property
    def visible(self):
        start = self.page*self.PAGE_SIZE
        return self.entries[start:start+self.PAGE_SIZE]

    def browse(self, directory):
        import os
        directory = Path(directory).resolve(strict=True)
        root = directory if self.root is None else self.root
        directory.relative_to(root)
        entries, truncated = [], False
        with os.scandir(directory) as scan:
            for count, item in enumerate(scan):
                if count >= self.LIMIT:
                    truncated = True
                    break
                if item.name.startswith('.') or item.is_symlink(): continue
                if item.is_dir(follow_symlinks=False) or (item.is_file(follow_symlinks=False) and
                                                         Path(item.name).suffix.lower() in ('.mp4', '.avi')):
                    entries.append(Path(item.path))
        self.root, self.directory = root, directory
        self.entries = sorted(entries, key=lambda p: (not p.is_dir(), p.name.casefold()))
        self.page = 0
        self.message = ('Folder is large; showing first 200 entries. Use a smaller media folder.' if truncated
                        else 'Choose H.264 MP4 or MJPEG AVI' if entries else 'No MP4 or AVI files in this folder')

    def choose(self, row):
        if type(row) is not int or not 0 <= row < len(self.visible): raise ValueError('No file selected')
        path = self.visible[row]
        if self.directory is None or path.is_dir():
            self.browse(path)
            return None
        resolved = path.resolve(strict=True)
        resolved.relative_to(self.root)
        if path.is_symlink() or not resolved.is_file() or resolved.suffix.lower() not in ('.mp4', '.avi'):
            raise ValueError('Selected media file is no longer available')
        return resolved

    def up(self):
        if self.directory is None or self.directory == self.root: self.home()
        else: self.browse(self.directory.parent)

    def turn(self, delta):
        self.page = max(0, min(max(0, (len(self.entries)-1)//self.PAGE_SIZE), self.page+delta))
