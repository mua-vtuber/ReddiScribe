"""Central LLM task scheduler with exclusive mode support."""

import logging
from typing import Optional, Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger("reddiscribe")


class TaskCoordinator(QObject):
    """Manages LLM task scheduling with exclusive access for writer polish stage.

    Two task modes:
    - Normal: concurrent tasks (reader translations, writer draft)
    - Exclusive: sole access (writer polish) - waits for normal tasks, blocks new ones

    Usage:
        coordinator = TaskCoordinator()

        # Normal task
        if coordinator.request_normal("title_translate", on_start_callback):
            start_work()  # can proceed immediately
        # else: queued, callback will be called when exclusive finishes

        # Exclusive task
        if coordinator.request_exclusive("writer_polish", on_start_callback):
            start_exclusive_work()  # can proceed immediately
        # else: queued, callback will be called when all normal tasks finish

        # When done
        coordinator.finish_normal("title_translate")
        coordinator.finish_exclusive()
    """

    exclusive_started = pyqtSignal()
    exclusive_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._normal_tasks: set[str] = set()
        self._exclusive_task: Optional[str] = None
        self._pending_exclusive: Optional[tuple[str, Callable]] = None
        self._pending_normal: list[tuple[str, Callable]] = []

    def is_exclusive_active(self) -> bool:
        """Check if an exclusive task is currently running."""
        return self._exclusive_task is not None

    def has_normal_tasks(self) -> bool:
        """Check if any normal tasks are currently running."""
        return bool(self._normal_tasks)

    def is_exclusive_pending(self) -> bool:
        """Check if an exclusive task is waiting to start."""
        return self._pending_exclusive is not None

    def request_normal(self, task_id: str, callback: Callable) -> bool:
        """Request to start a normal (concurrent) task.

        Args:
            task_id: Unique identifier for this task.
            callback: Called when task is cleared to start (if queued).

        Returns:
            True if task can proceed immediately, False if queued.
        """
        if self._exclusive_task is not None:
            logger.debug(f"Normal task '{task_id}' queued (exclusive active)")
            self._pending_normal.append((task_id, callback))
            return False

        self._normal_tasks.add(task_id)
        logger.debug(f"Normal task '{task_id}' started (active: {len(self._normal_tasks)})")
        return True

    def request_exclusive(self, task_id: str, callback: Callable) -> bool:
        """Request exclusive access for a task.

        If no other tasks are running, starts immediately.
        If normal tasks are running, queues until they all finish.

        Args:
            task_id: Unique identifier for this task.
            callback: Called when task is cleared to start (if queued).

        Returns:
            True if task can proceed immediately, False if queued.
        """
        if not self._normal_tasks and self._exclusive_task is None:
            self._exclusive_task = task_id
            self.exclusive_started.emit()
            logger.debug(f"Exclusive task '{task_id}' started immediately")
            return True

        logger.debug(
            f"Exclusive task '{task_id}' queued "
            f"(normal active: {len(self._normal_tasks)}, "
            f"exclusive active: {self._exclusive_task})"
        )
        self._pending_exclusive = (task_id, callback)
        return False

    def finish_normal(self, task_id: str):
        """Mark a normal task as finished.

        If all normal tasks are done and an exclusive task is pending,
        the exclusive task will be started automatically.

        Args:
            task_id: The task identifier to finish.
        """
        self._normal_tasks.discard(task_id)
        logger.debug(
            f"Normal task '{task_id}' finished "
            f"(remaining: {len(self._normal_tasks)})"
        )

        # If all normal tasks done and exclusive is waiting, start it
        if not self._normal_tasks and self._pending_exclusive:
            exc_id, exc_callback = self._pending_exclusive
            self._pending_exclusive = None
            self._exclusive_task = exc_id
            self.exclusive_started.emit()
            logger.debug(f"Exclusive task '{exc_id}' auto-started (all normal done)")
            exc_callback()

    def finish_exclusive(self):
        """Mark the exclusive task as finished.

        Starts all pending normal tasks automatically.
        """
        task_id = self._exclusive_task
        self._exclusive_task = None
        self.exclusive_finished.emit()
        logger.debug(f"Exclusive task '{task_id}' finished")

        # Resume all pending normal tasks
        pending = self._pending_normal[:]
        self._pending_normal.clear()
        for norm_id, norm_callback in pending:
            self._normal_tasks.add(norm_id)
            logger.debug(f"Normal task '{norm_id}' auto-started (exclusive done)")
            norm_callback()

    def cancel_exclusive(self):
        """Cancel the current or pending exclusive task.

        If exclusive is running, it ends immediately.
        If exclusive is pending, it is removed from the queue.
        In both cases, pending normal tasks are started.
        """
        if self._exclusive_task:
            logger.debug(f"Exclusive task '{self._exclusive_task}' cancelled")
            self._exclusive_task = None
            self.exclusive_finished.emit()

            # Resume pending normal tasks
            pending = self._pending_normal[:]
            self._pending_normal.clear()
            for norm_id, norm_callback in pending:
                self._normal_tasks.add(norm_id)
                logger.debug(f"Normal task '{norm_id}' auto-started (exclusive cancelled)")
                norm_callback()
        elif self._pending_exclusive:
            logger.debug(f"Pending exclusive task '{self._pending_exclusive[0]}' cancelled")
            self._pending_exclusive = None
