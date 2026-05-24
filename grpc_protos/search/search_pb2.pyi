from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StatusCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OK: _ClassVar[StatusCode]
    NOT_FOUND: _ClassVar[StatusCode]
    INVALID_ARGUMENT: _ClassVar[StatusCode]
    INTERNAL: _ClassVar[StatusCode]
OK: StatusCode
NOT_FOUND: StatusCode
INVALID_ARGUMENT: StatusCode
INTERNAL: StatusCode

class Document(_message.Message):
    __slots__ = ("content", "file_url", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    FILE_URL_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    content: str
    file_url: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, content: _Optional[str] = ..., file_url: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class QueryRequest(_message.Message):
    __slots__ = ("project", "query", "filters")
    class FiltersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    project: str
    query: str
    filters: _containers.ScalarMap[str, str]
    def __init__(self, project: _Optional[str] = ..., query: _Optional[str] = ..., filters: _Optional[_Mapping[str, str]] = ...) -> None: ...

class StringList(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, values: _Optional[_Iterable[str]] = ...) -> None: ...

class QueryResponse(_message.Message):
    __slots__ = ("status", "file_name_to_chunk_ids", "error")
    class FileNameToChunkIdsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: StringList
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[StringList, _Mapping]] = ...) -> None: ...
    STATUS_FIELD_NUMBER: _ClassVar[int]
    FILE_NAME_TO_CHUNK_IDS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status: StatusCode
    file_name_to_chunk_ids: _containers.MessageMap[str, StringList]
    error: str
    def __init__(self, status: _Optional[_Union[StatusCode, str]] = ..., file_name_to_chunk_ids: _Optional[_Mapping[str, StringList]] = ..., error: _Optional[str] = ...) -> None: ...

class StoreRequest(_message.Message):
    __slots__ = ("project", "source_file_name")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FILE_NAME_FIELD_NUMBER: _ClassVar[int]
    project: str
    source_file_name: str
    def __init__(self, project: _Optional[str] = ..., source_file_name: _Optional[str] = ...) -> None: ...

class StoreResponse(_message.Message):
    __slots__ = ("status", "error")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status: StatusCode
    error: str
    def __init__(self, status: _Optional[_Union[StatusCode, str]] = ..., error: _Optional[str] = ...) -> None: ...
