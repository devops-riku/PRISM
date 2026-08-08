"""Pushing a notification the moment it is written.

Polling every twenty seconds is a fine floor and a poor ceiling: a quotation
that lands the second after a tick sits unannounced for the rest of it. This is
the push - one in-process registry of who is listening, and a `publish` that
`inbox.deliver` calls after a note is safely committed to SQL.

Deliberately small, and deliberately not a delivery guarantee:

  * **Database first, socket second.** The note is written before anybody is told
    about it. A dropped frame costs a moment's latency; a socket that succeeded
    where the write failed would be a notification about something that does
    not exist.
  * **One process.** PRISM is one uvicorn. There is no broker here and there
    should not be one until there is a second process to talk to.
  * **The poll stays.** A client that loses its socket, sleeps, or connects
    through something that eats upgrades still gets its mail on a slow timer.
    A push nobody can fall back from is a push that eventually loses a message
    and never notices.

Addressed exactly as the inbox is: by workspace and person key. A listener only
ever hears about notes written into their own file, because that is the only
thing this ever sends.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Dict, Set, Tuple

logger = logging.getLogger("prism.hub")

__all__ = ["subscribe", "unsubscribe", "publish", "listeners"]

#: (workspace, person) -> the queues currently listening for it. A person with
#: two tabs open is two queues.
_rooms: Dict[Tuple[str, str], Set[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
_lock = threading.RLock()


def subscribe(workspace: str, person: str, queue: asyncio.Queue) -> None:
    loop = asyncio.get_running_loop()
    with _lock:
        _rooms.setdefault((workspace, person), set()).add((loop, queue))


def unsubscribe(workspace: str, person: str, queue: asyncio.Queue) -> None:
    with _lock:
        room = _rooms.get((workspace, person))
        if not room:
            return
        for entry in list(room):
            if entry[1] is queue:
                room.discard(entry)
        if not room:
            _rooms.pop((workspace, person), None)


def listeners(workspace: str = "", person: str = "") -> int:
    with _lock:
        if workspace and person:
            return len(_rooms.get((workspace, person), ()))
        return sum(len(room) for room in _rooms.values())


def publish(workspace: str, person: str, payload: dict) -> int:
    """Hand one payload to everybody listening as that person. Never raises.

    `call_soon_threadsafe` because a note can be written from a worker thread -
    a background job, the restart sweep - while the socket belongs to the event
    loop. Putting straight onto the queue from the wrong thread is the kind of
    bug that shows up once a week in production and never in a test.
    """
    sent = 0
    with _lock:
        room = list(_rooms.get((workspace, person), ()))

    for loop, queue in room:
        try:
            if loop.is_closed():
                continue
            loop.call_soon_threadsafe(queue.put_nowait, payload)
            sent += 1
        except (RuntimeError, asyncio.QueueFull) as exc:  # pragma: no cover
            logger.debug("Could not push to a listener: %s", exc)

    return sent
