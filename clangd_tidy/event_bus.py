import asyncio
import logging
from typing import Any, Callable, Coroutine, List, Type, Tuple, TypeVar, TypeAlias


T = TypeVar("T")
Handler: TypeAlias = Callable[[T], Coroutine[Any, Any, Any]]
Listener: TypeAlias = Tuple[Type[T], Handler[T]]


class EventBus:
    """
    A simple asynchronous event bus for dispatching events to registered listeners.
    Events are expected to be instances of LSP message types or similar data structures.
    """

    def __init__(self):
        self._listeners: List[Listener[Any]] = []
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._finished = asyncio.Future[None]()
        self._handler_tasks: set[asyncio.Task[Any]] = set()

    def subscribe(self, event_type: Type[T], handler: Handler[T]):
        """
        Registers a handler coroutine for a specific event type.

        Args:
            event_type: The type of event (e.g., an LSP message class) to subscribe to.
            handler: An async callable that will be called when an event of the given type is published.
        """
        self._listeners.append((event_type, handler))

    def publish(self, event: Any):
        """
        Publishes an event to the bus. The event will be put into a queue
        and dispatched asynchronously.

        Args:
            event: The event instance (e.g., an LSP message object) to publish.
        """
        self._queue.put_nowait(event)

    def _check_handler_task_exception(self, task: asyncio.Task[Any]):
        """Check if a handler task raised an exception and log it."""
        if not task.cancelled():
            try:
                task.result()
            except Exception as exc:
                logging.exception(
                    "Error in event handler task",
                    exc_info=exc,
                )
                self._finished.set_exception(exc)

    async def run(self):
        """
        The main dispatch loop that continuously pulls events from the queue
        and dispatches them to registered listeners.
        """
        while not self._finished.done():
            # Get the next event from the queue (or exit early if finished)
            event_task = asyncio.create_task(self._queue.get())
            done, _ = await asyncio.wait(
                {event_task, self._finished}, return_when=asyncio.FIRST_COMPLETED
            )

            if self._finished in done:
                # If the finished future is done, cancel the event task. We only set
                # _finished in case of error, so propagate the exception.
                event_task.cancel()
                break

            event = event_task.result()
            try:
                for event_type, handler in self._listeners:
                    if isinstance(event, event_type):
                        # Schedule handler as a separate task to avoid blocking the
                        # dispatch loop. This avoids deadlocks if a handler only
                        # completes after another event is processed.
                        task = asyncio.create_task(handler(event))
                        task.add_done_callback(self._handler_tasks.discard)
                        task.add_done_callback(self._check_handler_task_exception)
                        self._handler_tasks.add(task)
            except (Exception, asyncio.CancelledError) as e:
                logging.error(
                    f"Error dispatching event {type(event).__name__}: {e}",
                    exc_info=e,
                )
            finally:
                self._queue.task_done()

        # Cancel any remaining handler tasks
        for task in self._handler_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logging.debug("Stopping EventBus dispatch loop...")
        return await self._finished  # Propagate any exception
