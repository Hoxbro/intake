import re
from dataclasses import dataclass, field
from typing import ClassVar

import fsspec

from intake.readers.utils import subclasses


@dataclass
class Base:
    kwargs: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    mimetypes: ClassVar = set()
    extensions: ClassVar = set()


@dataclass
class FileData(Base):
    url: str = ""
    storage_option: dict = field(default_factory=dict)
    _filelist: ClassVar = None

    @property
    def filelist(self):
        if self._filelist is None:
            if isinstance(self.url, (list, tuple)):
                self._filelist = self.url
            else:
                self._filelist = fsspec.core.get_fs_token_paths(self.url, storage_options=self.storage_option)[2]
        return self._filelist


class Service:
    ...


class Catalog:
    ...


class Parquet(FileData):
    extensions = {"parq", "parquet", "/"}
    mimetypes = {"application/vnd.apache.parquet"}
    structure = {"table", "nested"}


class CSV(FileData):
    extensions = {"csv", "txt", "tsv"}
    mimetypes = {"text/csv", "application/csv", "application/vnd.ms-excel"}
    structure = {"table"}


class Text(FileData):
    extensions = {"txt", "text"}
    mimetypes = {"text/.*"}
    structure = {"sequence"}


@dataclass
class SQLQuery(Service):
    structure: ClassVar = {"sequence", "table"}
    conn: str
    query: str


class CatalogFile(Catalog, FileData):
    extensions = {"yaml", "yml"}
    mimetypes = {"text/yaml"}


@dataclass
class CatalogAPI(Catalog, Service):
    api_root: str


class YAMLFile(FileData):
    extensions = {"yaml", "yml"}
    mimetypes = {"text/yaml"}
    structure = {"nested"}


class JSONFile(FileData):
    extensions = {"json"}
    mimetypes = {"text/json", "application/json"}
    structure = {"nested", "table"}


def recommend(url=None, mime=None):
    out = set()
    if url is None and mime is None:
        raise ValueError
    if mime:
        for cls in subclasses(Base):
            if any(re.match(m, mime) for m in cls.mimetypes):
                out.add(cls)
    if url:
        # urlparse to remove query parts?
        for cls in subclasses(Base):
            if any(url.endswith(m) for m in cls.extensions):
                out.add(cls)
    return out
