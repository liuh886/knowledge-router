#!/usr/bin/env python3
"""Race-resistant file access rooted at a verified directory."""

from __future__ import annotations

import os
import stat
import time
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

_REPARSE_ATTRIBUTE = 0x400


def is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def ensure_directory_tree(base: Path, target: Path) -> Path:
    """Explicit provisioning only: create a bounded chain, refusing linked components."""
    base = base.expanduser().resolve(strict=True)
    target = target.expanduser().absolute()
    try:
        relative = target.relative_to(base)
    except ValueError:
        raise ValueError(f"Directory target must stay below its trusted base: {target}") from None
    with ExitStack() as pins:
        pins.enter_context(SecureParent(base, ".provision-anchor", allow_hidden=True))
        current = base
        for part in relative.parts:
            if part in {"", ".", ".."}:
                raise ValueError(f"Unsafe directory component: {part}")
            candidate = current / part
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                try:
                    candidate.mkdir()
                except FileExistsError:
                    pass
                info = candidate.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse(info):
                raise ValueError(f"Directories cannot be links or reparse points: {candidate}")
            relative_anchor = (candidate.relative_to(base) / ".provision-anchor").as_posix()
            pins.enter_context(SecureParent(base, relative_anchor, allow_hidden=True))
            current = candidate
    return target


def validate_relative_path(relative_path: str, *, allow_hidden: bool = False) -> PurePosixPath:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(
            (part.startswith(".") and not allow_hidden) or ":" in part for part in relative.parts
        )
    ):
        raise ValueError(f"Unsafe relative path: {relative_path}")
    return relative


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x80
    _FILE_LIST_DIRECTORY = 0x1
    _FILE_SHARE_READ = 0x1
    _FILE_SHARE_WRITE = 0x2
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x10
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = (wintypes.HANDLE,)
    _close_handle.restype = wintypes.BOOL
    _get_final_path = _kernel32.GetFinalPathNameByHandleW
    _get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    _get_final_path.restype = wintypes.DWORD
    _get_information = _kernel32.GetFileInformationByHandle
    _get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    _get_information.restype = wintypes.BOOL


def _normalized_windows_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def _windows_final_path(handle: Any) -> str:
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        written = _get_final_path(handle, buffer, size, 0)
        if written == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if written < size:
            return _normalized_windows_path(buffer.value)
        size = written + 1


def _windows_information(handle: Any) -> Any:
    information = _ByHandleFileInformation()
    if not _get_information(handle, ctypes.byref(information)):
        raise ctypes.WinError(ctypes.get_last_error())
    return information


def _windows_open_raw(
    path: Path,
    *,
    access: int,
    share: int,
    disposition: int,
    directory: bool,
    retry_seconds: float = 0.0,
    allow_hardlinks: bool = False,
) -> Any:
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    deadline = time.monotonic() + retry_seconds
    while True:
        handle = _create_file(str(path), access, share, None, disposition, flags, None)
        if handle != _INVALID_HANDLE_VALUE:
            break
        error = ctypes.get_last_error()
        if error not in {32, 33} or time.monotonic() >= deadline:
            raise ctypes.WinError(error)
        time.sleep(0.01)
    try:
        information = _windows_information(handle)
        if information.file_attributes & _REPARSE_ATTRIBUTE:
            raise ValueError(f"Symlink/reparse points are not allowed: {path}")
        is_directory = bool(information.file_attributes & _FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != directory:
            raise ValueError(f"Unexpected filesystem object type: {path}")
        if _windows_final_path(handle) != _normalized_windows_path(str(path.resolve())):
            raise RuntimeError(f"Filesystem object changed while opening: {path}")
        if not directory and information.number_of_links > 1 and not allow_hardlinks:
            raise ValueError(f"Hard-linked targets are not allowed: {path}")
        return handle
    except Exception:
        _close_handle(handle)
        raise


class SecureParent(AbstractContextManager["SecureParent"]):
    """Pin every parent component and open the final file relative to that chain."""

    def __init__(self, root: Path, relative_path: str, *, allow_hidden: bool = False) -> None:
        self.root = root.expanduser().resolve(strict=True)
        self.relative = validate_relative_path(relative_path, allow_hidden=allow_hidden)
        self.parent = self.root.joinpath(*self.relative.parts[:-1])
        self.path = self.parent / self.relative.name
        self._handles: list[Any] = []
        self._fds: list[int] = []

    def __enter__(self) -> SecureParent:
        if not self.root.is_dir():
            raise FileNotFoundError(f"Root directory does not exist: {self.root}")
        try:
            if os.name == "nt":
                current = self.root
                paths = [current]
                for part in self.relative.parts[:-1]:
                    current /= part
                    paths.append(current)
                for directory in paths:
                    handle = _windows_open_raw(
                        directory,
                        # Denying DELETE sharing pins the directory. Requesting
                        # DELETE access ourselves is unnecessary and conflicts
                        # with ordinary readers that also deny DELETE sharing.
                        # Attribute-only opens do not enforce this sharing lock;
                        # LIST_DIRECTORY is required to retain rename protection.
                        access=_FILE_READ_ATTRIBUTES | _FILE_LIST_DIRECTORY,
                        share=_FILE_SHARE_READ | _FILE_SHARE_WRITE,
                        disposition=_OPEN_EXISTING,
                        directory=True,
                        retry_seconds=30.0,
                    )
                    self._handles.append(handle)
            else:
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(self.root, flags)
                self._fds.append(descriptor)
                for part in self.relative.parts[:-1]:
                    descriptor = os.open(part, flags, dir_fd=descriptor)
                    self._fds.append(descriptor)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for descriptor in reversed(self._fds):
            os.close(descriptor)
        self._fds.clear()
        if os.name == "nt":
            for handle in reversed(self._handles):
                _close_handle(handle)
        self._handles.clear()

    def _validate_descriptor(
        self, descriptor: int, path: Path, *, allow_hardlinks: bool = False
    ) -> None:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or is_reparse(information):
            raise ValueError(f"Capture target is not a regular file: {path}")
        if information.st_nlink > 1 and not allow_hardlinks:
            raise ValueError(f"Hard-linked targets are not allowed: {path}")

    def open_existing(
        self,
        *,
        writable: bool,
        exclusive_writes: bool = True,
        exclusive_readers: bool = False,
        name: str | None = None,
        allow_hardlinks: bool = False,
    ) -> int:
        if writable and allow_hardlinks:
            raise ValueError("Hardlink permission is restricted to read-only source handles")
        target_name = name or self.relative.name
        if PurePosixPath(target_name).name != target_name or target_name in {
            "",
            ".",
            "..",
        }:
            raise ValueError(f"Unsafe sibling filename: {target_name}")
        target_path = self.parent / target_name
        if os.name == "nt":
            access = _GENERIC_READ | (_GENERIC_WRITE if writable else 0)
            share = (
                _FILE_SHARE_READ
                if writable and exclusive_writes
                else _FILE_SHARE_READ | _FILE_SHARE_WRITE
            )
            if exclusive_readers:
                share = 0
            handle = _windows_open_raw(
                target_path,
                access=access,
                share=share,
                disposition=_OPEN_EXISTING,
                directory=False,
                allow_hardlinks=allow_hardlinks,
            )
            flags = os.O_RDWR if writable else os.O_RDONLY
            flags |= getattr(os, "O_BINARY", 0)
            try:
                descriptor = msvcrt.open_osfhandle(int(handle), flags)
            except Exception:
                _close_handle(handle)
                raise
        else:
            if (writable and exclusive_writes) or exclusive_readers:
                raise RuntimeError(
                    "Atomic append capture currently requires Windows exclusive file sharing"
                )
            flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target_name, flags, dir_fd=self._fds[-1])
        try:
            self._validate_descriptor(descriptor, target_path, allow_hardlinks=allow_hardlinks)
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def create_new(self, *, mode: int = 0o600, name: str | None = None) -> int:
        target_name = name or self.relative.name
        if PurePosixPath(target_name).name != target_name or target_name in {
            "",
            ".",
            "..",
        }:
            raise ValueError(f"Unsafe sibling filename: {target_name}")
        target_path = self.parent / target_name
        if os.name == "nt":
            handle = _windows_open_raw(
                target_path,
                access=_GENERIC_READ | _GENERIC_WRITE,
                share=_FILE_SHARE_READ,
                disposition=_CREATE_NEW,
                directory=False,
            )
            flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
            try:
                descriptor = msvcrt.open_osfhandle(int(handle), flags)
            except Exception:
                _close_handle(handle)
                raise
        else:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target_name, flags, mode, dir_fd=self._fds[-1])
        try:
            self._validate_descriptor(descriptor, target_path)
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def read_bytes(self, *, name: str | None = None, allow_hardlinks: bool = False) -> bytes:
        """Read one validated sibling while reusing this live parent guard."""
        if not self._handles and not self._fds:
            raise RuntimeError("Reading requires an active directory guard")
        descriptor = self.open_existing(writable=False, name=name, allow_hardlinks=allow_hardlinks)
        try:
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)


def secure_read_bytes(root: Path, relative_path: str) -> bytes:
    with SecureParent(root, relative_path) as parent:
        return parent.read_bytes()


def secure_read_source_bytes(root: Path, relative_path: str) -> bytes:
    """Read an explicitly registered source; hardlinks do not redirect its rooted path.

    Symlinks/reparse points, traversal and non-regular files remain refused.
    Control records and mutation planning must use the strict reader above.
    """
    with SecureParent(root, relative_path) as parent:
        return parent.read_bytes(allow_hardlinks=True)


def secure_create_bytes(root: Path, relative_path: str, payload: bytes) -> None:
    with SecureParent(root, relative_path) as parent:
        descriptor = parent.create_new()
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError(f"Short write: wrote {written} of {len(payload)} bytes")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
