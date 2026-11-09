"""Shared descriptor-relative filesystem operations for local control files."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat


class Refused(ValueError):
    pass


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise Refused("Symlinked campaign path")


def owned_directory(root):
    """Traverse every absolute component using directory descriptors."""
    if type(root) is int:
        return os.dup(root)
    root = Path(root).absolute()
    safe_path(root)
    if '..' in root.parts:
        raise Refused('Owned root must be canonical')
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise Refused('Owned root or its ancestor is linked or unsafe') from None


def owned_mkdir(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise Refused('Unsafe owned directory path')
    descriptor = owned_directory(root)
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
    except OSError:
        raise Refused('Owned directory path is linked or unsafe') from None
    finally:
        os.close(descriptor)


def owned_file(root, relative, flags):
    """Open below a trusted directory without following any child symlink."""
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise Refused('Unsafe owned file path')
    directory = None
    try:
        directory = owned_directory(root)
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        descriptor = os.open(relative.parts[-1], (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        attributes = os.fstat(descriptor)
        if not stat.S_ISREG(attributes.st_mode) or attributes.st_nlink != 1:
            os.close(descriptor)
            raise Refused('Owned input/output must be a single-link regular file')
        if flags & os.O_TRUNC:
            os.ftruncate(descriptor, 0)  # Only after ownership/type/link checks.
        return descriptor
    except OSError:
        raise Refused('Owned input/output is linked, missing or unsafe') from None
    finally:
        if directory is not None:
            os.close(directory)


def read_owned(root, relative, limit=2_000_000):
    with os.fdopen(owned_file(root, relative, os.O_RDONLY), 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Refused('Owned input/output exceeds size bound')
    return data


def write_owned(root, relative, data, *, exclusive=False):
    data = data.encode('utf-8') if isinstance(data, str) else data
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    with os.fdopen(owned_file(root, relative, flags), 'wb') as stream:
        stream.write(data)


@contextmanager
def new_owned_directory(path):
    """Create a directory exclusively and retain its descriptor for writes."""
    path = Path(path).absolute()
    if not path.name or '..' in path.parts:
        raise Refused('New directory path must be canonical')
    parent = owned_directory(path.parent)
    try:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            raise Refused('Destination already exists') from None
        directory = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield directory
        finally:
            os.close(directory)
    finally:
        os.close(parent)
