"""测试 _retry 模块的重试逻辑、暂时性/永久性错误区分"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.memory._retry import (
    async_retry,
    async_retry_or_default,
    is_transient_error,
)


class TestIsTransientError:
    def test_timeout_is_transient(self):
        assert is_transient_error(asyncio.TimeoutError())

    def test_connection_error_is_transient(self):
        assert is_transient_error(ConnectionError("connection refused"))

    def test_os_error_is_transient(self):
        assert is_transient_error(OSError("too many files"))

    def test_401_is_permanent(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 401

        assert not is_transient_error(MockHTTPError())

    def test_403_is_permanent(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 403

        assert not is_transient_error(MockHTTPError())

    def test_408_is_transient(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 408

        assert is_transient_error(MockHTTPError())

    def test_429_is_transient(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 429

        assert is_transient_error(MockHTTPError())

    def test_500_is_transient(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 500

        assert is_transient_error(MockHTTPError())

    def test_503_is_transient(self):
        class MockHTTPError(Exception):
            def __init__(self):
                self.status_code = 503

        assert is_transient_error(MockHTTPError())

    def test_unknown_exception_is_transient(self):
        assert is_transient_error(RuntimeError("unexpected"))

    def test_http_status_via_status_attr(self):
        class Err(Exception):
            def __init__(self):
                self.status = 502

        assert is_transient_error(Err())

    def test_http_status_via_http_status_attr(self):
        class Err(Exception):
            def __init__(self):
                self.http_status = 500

        assert is_transient_error(Err())

    def test_http_status_via_code_attr(self):
        class Err(Exception):
            def __init__(self):
                self.code = 429

        assert is_transient_error(Err())

    def test_no_http_status_unknown_is_transient(self):
        class PlainError(Exception):
            pass

        assert is_transient_error(PlainError("generic"))


class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        func = AsyncMock(return_value="ok")
        result = await async_retry(func, max_retries=2, timeout=5, operation_name="test")
        assert result == "ok"
        func.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_on_transient_error_then_success(self):
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary")
            return "ok"

        result = await async_retry(flaky, max_retries=3, timeout=5, operation_name="flaky")
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raise_after_all_retries_exhausted(self):
        async def always_fails():
            raise ConnectionError("persistent")

        with pytest.raises(ConnectionError):
            await async_retry(always_fails, max_retries=2, timeout=5, operation_name="fail")

    @pytest.mark.asyncio
    async def test_permanent_error_no_retry(self):
        call_count = 0

        class AuthError(Exception):
            def __init__(self):
                self.status_code = 401

        async def auth_fails():
            nonlocal call_count
            call_count += 1
            raise AuthError()

        with pytest.raises(AuthError):
            await async_retry(auth_fails, max_retries=3, timeout=5, operation_name="auth")
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_retries(self):
        call_count = 0

        async def slow():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(10)
            return "never"

        with pytest.raises(asyncio.TimeoutError):
            await async_retry(slow, max_retries=1, timeout=0.01, operation_name="slow")
        assert call_count == 2


class TestAsyncRetryOrDefault:
    @pytest.mark.asyncio
    async def test_returns_default_on_failure(self):
        async def fails():
            raise RuntimeError("fail")

        result = await async_retry_or_default(
            fails, max_retries=1, timeout=5, operation_name="test", default="fallback"
        )
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_returns_value_on_success(self):
        async def works():
            return "success"

        result = await async_retry_or_default(
            works, max_retries=1, timeout=5, operation_name="test", default="fallback"
        )
        assert result == "success"

    @pytest.mark.asyncio
    async def test_default_none_on_failure(self):
        async def fails():
            raise RuntimeError("fail")

        result = await async_retry_or_default(
            fails, max_retries=1, timeout=5, operation_name="test"
        )
        assert result is None
