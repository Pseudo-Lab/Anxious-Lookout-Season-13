"""Post blocks v1 wire shapes; not yet wired to application input validation.

Exported JSON Schema covers shapes/local bounds. Semantic validation, ownership,
normalization, projection and conversion are separate subsequent work.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

UUIDText = Annotated[str, StringConstraints(min_length=36, max_length=36, pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]
Color = Annotated[str, StringConstraints(min_length=7, max_length=7, pattern=r'^#[0-9a-fA-F]{6}$')]
Align = Literal['left', 'center', 'right']
Language = Literal['plain', 'bash', 'c', 'cpp', 'css', 'go', 'html', 'java',
                   'javascript', 'json', 'jsx', 'markdown', 'python', 'rust',
                   'sql', 'tsx', 'typescript', 'xml', 'yaml']


class Shape(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ColorMark(Shape):
    color: Color


Mark = Union[Literal['bold', 'italic', 'underline', 'strike', 'code'], ColorMark]


class CaptionRun(Shape):
    text: Annotated[str, Field(min_length=1, max_length=20000)]
    marks: Annotated[list[Mark], Field(max_length=6)] = Field(default_factory=list)


class Run(CaptionRun):
    link: str | None = None


class Node(Shape):
    id: UUIDText


class Paragraph(Node):
    type: Literal['paragraph']
    runs: Annotated[list[Run], Field(max_length=10000)]
    align: Align = 'left'


class CellParagraph(Node):
    type: Literal['paragraph']
    runs: Annotated[list[Run], Field(max_length=10000)]
    align: Literal['left'] = 'left'


class Heading(Node):
    type: Literal['heading']
    level: Annotated[int, Field(ge=1, le=6)]
    runs: Annotated[list[Run], Field(max_length=10000)]
    align: Align = 'left'


class ListItem(Node):
    blocks: Union[tuple[Paragraph], tuple[Paragraph, ListBlock]]


class ListBlock(Node):
    type: Literal['list']
    ordered: bool
    start: Annotated[int, Field(ge=1, le=9999)] | None = None
    items: Annotated[list[ListItem], Field(min_length=1, max_length=2000)]


class Quote(Node):
    type: Literal['quote']
    blocks: Annotated[list[Union[Paragraph, ListBlock]], Field(min_length=1, max_length=2000)]


class Divider(Node):
    type: Literal['divider']


class Code(Node):
    type: Literal['code']
    text: Annotated[str, Field(max_length=200000)]
    language: Language = 'plain'


class Image(Node):
    type: Literal['image']
    imageId: UUIDText
    alt: Annotated[str, Field(max_length=1000)] = ''
    caption: Annotated[list[CaptionRun], Field(max_length=10000)] = Field(default_factory=list)
    widthPercent: Literal[25, 50, 75, 100] = 100
    align: Align = 'left'


class Cell(Node):
    blocks: Annotated[list[CellParagraph], Field(min_length=1, max_length=2000)]
    align: Align = 'left'
    background: Color | None = None


class Row(Node):
    cells: Annotated[list[Cell], Field(min_length=1, max_length=20)]
    header: bool = False


class Table(Node):
    type: Literal['table']
    rows: Annotated[list[Row], Field(min_length=1, max_length=100)]


class Embed(Node):
    type: Literal['embed']
    provider: Literal['youtube']
    resourceId: Annotated[str, StringConstraints(min_length=11, max_length=11, pattern=r'^[A-Za-z0-9_-]{11}$')]
    caption: Annotated[list[CaptionRun], Field(max_length=10000)] = Field(default_factory=list)


ColumnBlock = Annotated[Union[Paragraph, Heading, Quote, ListBlock, Divider, Code, Image, Embed], Field(discriminator='type')]


class Column(Node):
    width: Annotated[int, Field(ge=1, le=4)]
    blocks: Annotated[list[ColumnBlock], Field(min_length=1, max_length=2000)]


class Columns(Node):
    type: Literal['columns']
    columns: Annotated[list[Column], Field(min_length=2, max_length=3)]


Block = Annotated[Union[Paragraph, Heading, Quote, ListBlock, Divider, Code, Image, Table, Columns, Embed], Field(discriminator='type')]


class Document(Shape):
    format: Literal['post-blocks']
    version: Literal[1]
    blocks: Annotated[list[Block], Field(min_length=1, max_length=500)]

    @field_validator('version', mode='before')
    @classmethod
    def require_integer_version(cls, value):
        # Literal equality otherwise accepts True == 1 before semantic checks
        # can inspect the original input. Do not echo rejected input in errors.
        if type(value) is not int:
            raise ValueError('Version must be an integer')
        return value


ListItem.model_rebuild()
ListBlock.model_rebuild()
Document.model_rebuild()


def export_schema():
    schema = Document.model_json_schema()
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    schema['$id'] = 'urn:anxious-lookout:post-blocks:1:input'
    schema['description'] = 'Input shapes only. See README semantic rules and golden.json; this schema alone does not authorize storage.'
    return schema
