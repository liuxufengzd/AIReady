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
    __slots__ = ("page_content", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    PAGE_CONTENT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    page_content: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, page_content: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

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

class QueryResponse(_message.Message):
    __slots__ = ("status", "documents", "error")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DOCUMENTS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status: StatusCode
    documents: _containers.RepeatedCompositeFieldContainer[Document]
    error: str
    def __init__(self, status: _Optional[_Union[StatusCode, str]] = ..., documents: _Optional[_Iterable[_Union[Document, _Mapping]]] = ..., error: _Optional[str] = ...) -> None: ...

class StoreRequest(_message.Message):
    __slots__ = ("project", "metadata_file_names")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FILE_NAMES_FIELD_NUMBER: _ClassVar[int]
    project: str
    metadata_file_names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, project: _Optional[str] = ..., metadata_file_names: _Optional[_Iterable[str]] = ...) -> None: ...

class StoreResponse(_message.Message):
    __slots__ = ("status", "error")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    status: StatusCode
    error: str
    def __init__(self, status: _Optional[_Union[StatusCode, str]] = ..., error: _Optional[str] = ...) -> None: ...
