"""Publish completed local media so the unprivileged result collector can read it."""

import os
import stat


def prepare_readable_artifact(directory):
    # mkdtemp stays private during processing. Only completed, flat artifacts
    # receive read/traverse access immediately before their atomic rename.
    if os.name != 'posix':
        return
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError('Expected regular artifact files')
        path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o044)
    directory.chmod(stat.S_IMODE(directory.stat().st_mode) | 0o055)
