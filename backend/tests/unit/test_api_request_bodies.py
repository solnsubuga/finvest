"""Every JSON request body can be sent as JSON.

Request bodies are strict `Contract`s, which FastAPI validates from the parsed JSON. Strict, an
enum or a date field takes only an enum or a date instance, which JSON can never carry, so
every request answers 422 `is_instance_of` (QA F-001, and the product types form after it). Such
a field must say `Field(strict=False)`, or convert the text itself with a `BeforeValidator`.
"""

import datetime
import enum
import typing
from collections.abc import Iterator

from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator, PlainValidator, SecretStr
from pydantic.fields import FieldInfo

from app.api.app import create_app
from app.infrastructure.settings import Settings

_NEEDS_TEXT = (enum.Enum, datetime.date, datetime.time, datetime.timedelta)


def _routes(routes: typing.Iterable[object]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        # Routers included in the app, at any depth.
        inner = getattr(getattr(route, "original_router", None), "routes", None)
        if inner is None:
            inner = getattr(route, "routes", None)
        if inner:
            yield from _routes(inner)


def _loosened(metadata: typing.Iterable[object], strict: bool | None) -> bool | None:
    for meta in metadata:
        if isinstance(meta, BeforeValidator | PlainValidator):
            return False
        inner = meta.metadata if isinstance(meta, FieldInfo) else []
        for item in [meta, *inner]:
            if isinstance(item, BeforeValidator | PlainValidator):
                return False
            value = getattr(item, "strict", None)
            if isinstance(value, bool):
                strict = value
    return strict


def _problems(model: type[BaseModel], path: str, seen: set[type[BaseModel]]) -> Iterator[str]:
    if model in seen:
        return
    seen.add(model)
    model_strict = bool(model.model_config.get("strict"))

    def check(annotation: object, strict: bool | None, where: str) -> Iterator[str]:
        origin = typing.get_origin(annotation)
        if origin is typing.Annotated:
            inner, *metadata = typing.get_args(annotation)
            yield from check(inner, _loosened(metadata, strict), where)
            return
        if origin is not None:
            for argument in typing.get_args(annotation):
                yield from check(argument, strict, where)
            return
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from _problems(annotation, where, seen)
            return
        if (
            isinstance(annotation, type)
            and issubclass(annotation, _NEEDS_TEXT)
            and (strict if strict is not None else model_strict)
        ):
            yield f"{where}: {annotation.__name__}"

    for name, field in model.model_fields.items():
        strict = _loosened(field.metadata, None)
        yield from check(field.annotation, strict, f"{path}.{name}")


def test_no_request_body_needs_an_instance_json_cannot_send() -> None:
    app = create_app(
        Settings(database_url=SecretStr("postgresql+asyncpg://bodies@localhost/bodies"))
    )
    seen: set[type[BaseModel]] = set()
    problems: list[str] = []
    for route in _routes(app.routes):
        body = route.body_field.field_info.annotation if route.body_field else None
        if isinstance(body, type) and issubclass(body, BaseModel):
            problems.extend(_problems(body, f"{route.path} {body.__name__}", seen))
    assert problems == [], "Give these fields Field(strict=False):\n" + "\n".join(problems)
