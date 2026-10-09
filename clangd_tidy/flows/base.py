import asyncio
import logging
from typing import Any, Coroutine, TypeVar

from ..lsp.messages import ClientCapabilities
from ..lsp.clangd import ClangdAsync
from ..event_bus import EventBus

T = TypeVar("T")


class BaseFlow:
    """
    A "Flow" represents a distinct, high-level operation or set of operations
    that interacts with the clangd LSP server to achieve a specific goal.

    Examples of flows include:
    - Analyzing a set of files for diagnostics.
    - Requesting and applying code actions.
    - Formatting a set of files.

    A single flow can spawn multiple asynchronous tasks to perform its work,
    and can track the state of these tasks to determine when the flow is complete.

    A BaseFlow provides convenience methods for managing its lifecycle, including:
    - Spawning tasks tied to the flow's lifecycle.
    - Resetting the flow state after a clangd crash.
    - Adding required client capabilities for the flow.
    - Checking clangd capabilities after initialization.
    """

    def __init__(
        self,
        clangd_client: ClangdAsync,
        event_bus: EventBus,
    ):
        self._clangd = clangd_client
        self._event_bus = event_bus
        self._futures: dict[Any, asyncio.Future[Any]] = {}

    async def bootstrap(self):
        """
        Bootstrap the flow state. Handles clangd start/restart.

        This should set-up any initial state required for the flow to run, clearing any stale state from
        previous runs. Any state that needs to be retained after clangd crash should be persisted by subclasses.

        Any state that needs to be retained after clangd crash should be persisted by subclasses.
        For example, a list of files to be processed.
        """
        await self._check_futures_purge_pending(log_errors=False)

    async def shutdown(self):
        """
        Cleans up the flow state. Called when the flow is being shut down.

        This should clean up any state that is no longer needed, and ensure that
        any pending tasks are cancelled and exceptions are logged.
        """
        await self._check_futures_purge_pending(log_errors=True)  # Log remaining errors

    def add_required_capabilities(
        self, capabilities: ClientCapabilities
    ) -> ClientCapabilities:
        """Adds any required client capabilities for this flow. Can be overridden by subclasses."""
        return capabilities

    def check_clangd_capabilities(self, init_result: Any) -> None:
        """Checks if clangd supports the capabilities required by this flow. Can be overridden by subclasses.

        The capabilities are passed as-is from the clangd initialize response."""
        pass

    def pending_futures(self) -> frozenset[asyncio.Future[Any]]:
        """Returns all pending futures handled by this flow."""
        return frozenset(
            awaitable for awaitable in self._futures.values() if not awaitable.done()
        )

    def completed(self) -> bool:
        """
        Returns True if the flow has completed all its work.

        This method considers certain exceptions, such as connection errors, as
        incomplete work that can be retried.
        """
        for future in self._futures.values():
            # Any incomplete future means the flow is not complete
            if not future.done():
                return False

            # Also several types of exceptions indicate incomplete work, as they can be
            # retried on flow restart.
            exception = future.exception()
            if exception is not None:
                if isinstance(exception, (ConnectionError, EOFError)):
                    # Communication errors - clangd crashed, let's retry
                    return False
                if isinstance(exception, asyncio.CancelledError):
                    # Cancelled futures were cancelled as part of flow reset, retry
                    return False
        return True

    async def run(self) -> None:
        """
        Runs the flow. This method can be overridden by subclasses to
        perform the main logic of the flow.
        """
        pass

    def _create_future(self, key: Any) -> asyncio.Future[Any]:
        """
        Creates a new future that is managed by the flow.

        Use this method to create futures for work that should be cancelled if
        the flow is reset (e.g., due to a clangd crash).

        Args:
        - key: A unique identifier for the future.

        Returns:
            The created future.

        Raises:
            KeyError if a future with the same key already exists.
        """
        if key in self._futures:
            raise KeyError(f"Future with key {key} already tracked")
        future: asyncio.Future[Any] = asyncio.Future()
        self._futures[key] = future
        return future

    def _resolve_future(
        self, key: Any, result: Any, ignore_duplicates: bool = False
    ) -> None:
        """
        Resolves a future tied to this flow's lifecycle.

        Args:
        - key: The unique identifier for the future.
        - result: The result to set on the future.

        Raises:
            KeyError if a future with the specified key does not exist.
            asyncio.InvalidStateError if the future is already done.
        """
        if key not in self._futures:
            raise KeyError(f"Future with key {key} not found")

        self._futures[key].set_result(result)

    def _spawn_task(
        self, key: Any, coro: Coroutine[Any, Any, T], limited: bool = True
    ) -> asyncio.Future[T]:
        """
        Spawns a new asyncio task tied to this flow's lifecycle.

        It automatically creates a future to track the task's completion and resolves
        it when the task completes.

        Args:
        - key: A unique identifier for the task.
        - coro: The coroutine to run in the new task.
        - limited: If True, the task will acquire a semaphore from the clangd client
          before running, limiting the number of concurrent tasks.

        Returns:
            The new task.

        Raises:
            KeyError if a task with the same key already exists.
        """
        if key in self._futures:
            raise KeyError(f"Task with key {key} already spawned")

        async def run_with_semaphore() -> T:
            async with self._clangd.get_semaphore():
                return await coro

        if limited:
            task = asyncio.create_task(run_with_semaphore())
        else:
            task = asyncio.create_task(coro)
        self._futures[key] = task
        return task

    async def _check_futures_purge_pending(self, log_errors: bool = True):
        """
        Checks all futures spawned by this flow and removes any that are pending.

        Logs an error for any futures that had an exception (other than CancelledError).
        """
        for key in list(self._futures.keys()):
            future = self._futures[key]
            future.cancel()  # Does nothing for already completed futures
            try:
                # Await it to ensure any exception is propagated
                await future
            except asyncio.CancelledError:
                # If the future was cancelled, remove the future from tracking to let it
                # be re-created in case of flow restart. We only persist completed
                # futures.
                del self._futures[key]
            except (ConnectionError, EOFError):
                # Similarly, futures that failed due to connection issues are likely
                # transient errors that will be fixed on flow restart. Clangd closes
                # the connection on crash, so we don't want to persist these errors.
                del self._futures[key]
            except Exception as e:
                if log_errors:
                    logging.exception(
                        f"Future with key {key} failed in {self.__class__.__name__}: {e}",
                        exc_info=e,
                    )
                # Otherwise silently ignore the error. This is done during
                # bootstrap to avoid logging errors for tasks that will be
                # retried anyway.
