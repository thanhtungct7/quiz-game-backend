import json
from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.content._etag import CONTENT_CACHE_CONTROL, conditional_json_response
from app.api.routes.content.courses import get_course_tree
from app.core.exceptions import CourseNotFoundError
from app.schemas.content.course_content import CourseTreeRead, LessonTreeRead, UnitTreeRead


def make_request(if_none_match: str | None = None):  # type: ignore[no-untyped-def]
    """Minimal stand-in for the Request objects the routes only read headers from."""

    class _Request:
        def __init__(self) -> None:
            self.headers = {} if if_none_match is None else {"If-None-Match": if_none_match}

    return _Request()


def make_tree(title: str = "English") -> CourseTreeRead:
    return CourseTreeRead(
        id="course-1",
        title=title,
        image_src="/en.svg",
        units=[
            UnitTreeRead(
                id="unit-1",
                title="Unit 1",
                description="d",
                order_index=1,
                lessons=[
                    LessonTreeRead(id="lesson-1", title="Cửa 1", order_index=1, challenge_count=10)
                ],
            )
        ],
    )


class FakeContentService:
    def __init__(self, tree: CourseTreeRead) -> None:
        self.tree = tree

    async def get_course_tree(self, course_id: str) -> CourseTreeRead:
        return self.tree


class FailingContentService:
    async def get_course_tree(self, course_id: str) -> NoReturn:
        raise CourseNotFoundError(course_id)


def test_conditional_response_returns_body_and_etag_when_client_has_nothing() -> None:
    response = conditional_json_response(make_request(), make_tree())  # type: ignore[arg-type]

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["Cache-Control"] == CONTENT_CACHE_CONTROL
    assert response.headers["ETag"]
    assert json.loads(response.body)["id"] == "course-1"


def test_conditional_response_returns_304_for_a_matching_etag() -> None:
    first = conditional_json_response(make_request(), make_tree())  # type: ignore[arg-type]
    etag = first.headers["ETag"]

    second = conditional_json_response(make_request(etag), make_tree())  # type: ignore[arg-type]

    assert second.status_code == status.HTTP_304_NOT_MODIFIED
    assert second.body == b""
    assert second.headers["ETag"] == etag


def test_conditional_response_tolerates_weak_and_listed_etags() -> None:
    etag = conditional_json_response(make_request(), make_tree()).headers["ETag"]  # type: ignore[arg-type]

    header = f'"stale", W/{etag}'
    response = conditional_json_response(make_request(header), make_tree())  # type: ignore[arg-type]

    assert response.status_code == status.HTTP_304_NOT_MODIFIED


def test_conditional_response_resends_when_the_content_changed() -> None:
    etag = conditional_json_response(make_request(), make_tree()).headers["ETag"]  # type: ignore[arg-type]

    response = conditional_json_response(make_request(etag), make_tree("Español"))  # type: ignore[arg-type]

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["ETag"] != etag


@pytest.mark.asyncio
async def test_get_course_tree_revalidates_instead_of_resending() -> None:
    service = FakeContentService(make_tree())

    first = await get_course_tree("course-1", make_request(), service)  # type: ignore[arg-type]
    second = await get_course_tree(
        "course-1",
        make_request(first.headers["ETag"]),  # type: ignore[arg-type]
        service,  # type: ignore[arg-type]
    )

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_304_NOT_MODIFIED


@pytest.mark.asyncio
async def test_get_course_tree_maps_course_not_found_to_404() -> None:
    with pytest.raises(HTTPException) as raised:
        await get_course_tree("missing", make_request(), FailingContentService())  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND
